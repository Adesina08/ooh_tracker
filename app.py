from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.utils import secure_filename
import os
import hashlib
import datetime
import ffmpeg
import librosa
import whisper
import re
import logging

app = Flask(__name__)

app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your-secret-key')
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'mp4', 'mov'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB limit

# Database configuration
db_params = {
    'dbname': os.environ.get('DB_NAME', 'ooh_tracker_db'),
    'user': os.environ.get('DB_USER', 'ooh_tracker_db_user'),
    'password': os.environ.get('DB_PASSWORD', 'bZvhR8NpLOxIXQRnSC7qt6tn9Ny7T6jf'),
    'host': os.environ.get('DB_HOST', 'dpg-d1m16indiees7389jq50-a.oregon-postgres.render.com'),
    'port': os.environ.get('DB_PORT', '5432')
}


# Ensure upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_db_connection():
    return psycopg2.connect(**db_params, cursor_factory=RealDictCursor)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Mock brand and SKU data (replace with actual database query if needed)
def get_brands_and_skus(category):
    # Example data; replace with database query
    data = {
        'snack': {'brands': ['Pringles', 'Lay’s'], 'skus': ['50g', '100g']},
        'meal': {'brands': ['Jollof', 'Poundo'], 'skus': ['plate', 'bowl']},
        'beverage': {'brands': ['Coca-Cola', 'Pepsi', 'Fanta'], 'skus': ['500ml', '1l']},
        'other': {'brands': ['Generic'], 'skus': ['unit']}
    }
    return data.get(category, {'brands': [], 'skus': []})

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT id, username FROM users WHERE email = %s AND password = %s', (email, hashed_password))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute('INSERT INTO users (username, email, password) VALUES (%s, %s, %s) RETURNING id',
                       (username, email, hashed_password))
            user_id = cur.fetchone()['id']
            conn.commit()
            session['user_id'] = user_id
            session['username'] = username
            flash('Registration successful!', 'success')
            return redirect(url_for('dashboard'))
        except psycopg2.IntegrityError:
            conn.rollback()
            flash('Email already registered', 'danger')
        finally:
            cur.close()
            conn.close()
    return render_template('register.html')

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT id FROM users WHERE email = %s', (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        if user:
            # Placeholder for sending reset email (implement with smtplib or SendGrid)
            flash('Password reset instructions sent to your email.', 'success')
        else:
            flash('No account found with that email.', 'danger')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Total entries
    cur.execute('SELECT COUNT(*) as count FROM consumption_records WHERE user_id = %s', (session['user_id'],))
    total_entries = cur.fetchone()['count']
    
    # Unique brands
    cur.execute('SELECT COUNT(DISTINCT brand) as count FROM consumption_records WHERE user_id = %s', (session['user_id'],))
    unique_brands = cur.fetchone()['count']
    
    # Average spending
    cur.execute('SELECT AVG(amount_paid) as avg FROM consumption_records WHERE user_id = %s', (session['user_id'],))
    avg_spending = cur.fetchone()['avg']
    
    # Locations visited
    cur.execute('SELECT COUNT(DISTINCT purchase_location) as count FROM consumption_records WHERE user_id = %s', (session['user_id'],))
    locations_visited = cur.fetchone()['count']
    
    # Top purchase location
    cur.execute('''
        SELECT purchase_location, COUNT(*) as count
        FROM consumption_records
        WHERE user_id = %s
        GROUP BY purchase_location
        ORDER BY count DESC
        LIMIT 1
    ''', (session['user_id'],))
    top_purchase_location = cur.fetchone()['purchase_location'] if cur.rowcount > 0 else None
    
    # Consumption by category
    cur.execute('''
        SELECT product_category, COUNT(*) as count, SUM(amount_paid) as total_spent
        FROM consumption_records
        WHERE user_id = %s
        GROUP BY product_category
    ''', (session['user_id'],))
    consumption_by_category = cur.fetchall()
    
    # Recent activities
    cur.execute('''
        SELECT 
            id, product_category, brand, sku, amount_paid, purchase_location,
            consume_location, with_whom, to_char(created_at, 'YYYY-MM-DD HH24:MI') as date,
            CASE 
                WHEN additional_brand IS NOT NULL THEN 'Yes'
                ELSE 'No'
            END as had_additional_items
        FROM consumption_records
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 5
    ''', (session['user_id'],))
    recent_activities = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template('dashboard.html',
                         user_name=session['username'],
                         total_entries=total_entries,
                         unique_brands=unique_brands,
                         avg_spending=avg_spending,
                         locations_visited=locations_visited,
                         top_purchase_location=top_purchase_location,
                         consumption_by_category=consumption_by_category,
                         recent_activities=recent_activities)

@app.route('/track')
def track():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('track.html')

@app.route('/instructions')
def instructions():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('instructions.html')

@app.route('/api/get-brands-and-skus')
def get_brands_and_skus_route():
    category = request.args.get('product_category')
    data = get_brands_and_skus(category)
    return jsonify({'brands': data['brands'], 'skus': data['skus']})

@app.route('/api/submit-consumption', methods=['POST'])
def submit_consumption():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'}), 401
    
    form_data = request.form
    file = request.files.get('photo') or request.files.get('video')
    filename = None
    
    if file and allowed_file(file.filename):
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = secure_filename(f"user_{session['user_id']}_{timestamp}.{file.filename.rsplit('.', 1)[1].lower()}")
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute('''
            INSERT INTO consumption_records (
                user_id, product_category, brand, sku, amount_paid, purchase_location,
                consume_location, with_whom, with_what, additional_brand, photo_path,
                latitude, longitude, accuracy
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            session['user_id'],
            form_data.get('product_category'),
            form_data.get('brand'),
            form_data.get('sku'),
            float(form_data.get('amount_paid')),
            form_data.get('purchase_location'),
            form_data.get('consume_location'),
            form_data.get('with_whom'),
            form_data.get('with_what'),
            form_data.get('additional_brand') or None,
            filename,
            form_data.get('latitude') or None,
            form_data.get('longitude') or None,
            form_data.get('accuracy') or None
        ))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        logger.error(f"Error submitting consumption: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/api/analyze-video', methods=['POST'])
def analyze_video():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'}), 401
    
    file = request.files.get('video')
    if not file or not allowed_file(file.filename):
        return jsonify({'success': False, 'message': 'Invalid or missing video file'}), 400
    
    if file.content_length > 10 * 1024 * 1024:
        return jsonify({'success': False, 'message': 'Video exceeds 10MB limit'}), 400
    
    # Save video
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = secure_filename(f"user_{session['user_id']}_{timestamp}.{file.filename.rsplit('.', 1)[1].lower()}")
    video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(video_path)
    
    try:
        # Validate video duration
        probe = ffmpeg.probe(video_path)
        duration = float(probe['format']['duration'])
        if duration > 60:
            os.remove(video_path)
            return jsonify({'success': False, 'message': 'Video exceeds 1-minute limit'}), 400
        
        # Extract audio
        audio_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{timestamp}.mp3")
        stream = ffmpeg.input(video_path)
        stream = ffmpeg.output(stream, audio_path, acodec='mp3', vn=True)
        ffmpeg.run(stream, overwrite_output=True)
        
        # Preprocess audio with Librosa
        y, sr = librosa.load(audio_path, sr=None)
        y, _ = librosa.effects.trim(y)  # Trim silence
        librosa.output.write_wav(audio_path.replace('.mp3', '.wav'), y, sr)
        
        # Transcribe with Whisper
        model = whisper.load_model('base')
        result = model.transcribe(audio_path.replace('.mp3', '.wav'))
        transcript = result['text'].lower()
        
        # Clean up temporary audio
        os.remove(audio_path)
        os.remove(audio_path.replace('.mp3', '.wav'))
        
        # Parse transcript
        data = parse_transcript(transcript)
        data['video_path'] = filename
        
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        logger.error(f"Error analyzing video: {str(e)}")
        if os.path.exists(video_path):
            os.remove(video_path)
        return jsonify({'success': False, 'message': str(e)}), 500

def parse_transcript(transcript):
    data = {
        'product_category': None,
        'brand': None,
        'sku': None,
        'amount_paid': None,
        'purchase_location': None,
        'consume_location': None,
        'with_whom': None,
        'with_what': 'no',
        'additional_brand': None
    }
    
    # Get known brands and SKUs
    categories = ['snack', 'meal', 'beverage', 'other']
    all_brands = []
    all_skus = []
    for category in categories:
        cat_data = get_brands_and_skus(category)
        all_brands.extend(cat_data['brands'])
        all_skus.extend(cat_data['skus'])
    
    # product_category
    category_keywords = {
        'snack': ['snack', 'chips', 'biscuit'],
        'meal': ['meal', 'food', 'lunch', 'dinner'],
        'beverage': ['beverage', 'drink', 'soda', 'juice'],
        'other': ['other']
    }
    for category, keywords in category_keywords.items():
        if any(keyword in transcript for keyword in keywords):
            data['product_category'] = category
            break
    
    # brand
    for brand in all_brands:
        if brand.lower() in transcript:
            data['brand'] = brand
            if not data['product_category']:
                for category, cat_data in category_keywords.items():
                    if brand in get_brands_and_skus(category)['brands']:
                        data['product_category'] = category
                        break
            break
    
    # sku
    sku_match = re.search(r'(\d+\.?\d*)\s*(ml|l|liter|kg|g|gram|ounce|oz|pack|large|medium|small)', transcript)
    if sku_match:
        sku_value = f"{sku_match.group(1)}{sku_match.group(2)}"
        if sku_value in all_skus:
            data['sku'] = sku_value
    
    # amount_paid
    amount_match = re.search(r'(\d+\.?\d*)\s*(naira|₦)', transcript)
    if amount_match:
        data['amount_paid'] = float(amount_match.group(1))
    
    # purchase_location
    purchase_keywords = {
        'supermarket': ['supermarket', 'shop', 'store', 'market'],
        'convenience_store': ['convenience store', 'corner shop'],
        'restaurant': ['restaurant', 'eatery', 'cafe'],
        'online': ['online', 'website']
    }
    for location, keywords in purchase_keywords.items():
        if any(keyword in transcript for keyword in keywords):
            data['purchase_location'] = location
            break
    
    # consume_location
    consume_keywords = {
        'home': ['home', 'house'],
        'work': ['work', 'office'],
        'play': ['play', 'park', 'party'],
        'school': ['school', 'class']
    }
    for location, keywords in consume_keywords.items():
        if any(keyword in transcript for keyword in keywords):
            data['consume_location'] = location
            break
    
    # with_whom
    if 'alone' in transcript:
        data['with_whom'] = 'alone'
    elif any(keyword in transcript for keyword in ['friends', 'family', 'colleagues', 'others', 'people']):
        data['with_whom'] = 'with_others'
    
    # with_what
    if any(keyword in transcript for keyword in ['yes', 'with another', 'with other item']):
        data['with_what'] = 'yes'
    
    # additional_brand
    if data['with_what'] == 'yes':
        for brand in all_brands:
            if brand.lower() in transcript and brand != data['brand']:
                data['additional_brand'] = brand
                break
        if not data['additional_brand']:
            words = transcript.split()
            for i, word in enumerate(words):
                if word in [data['brand'].lower()] if data['brand'] else []:
                    continue
                if word in all_brands or len(word) > 3:
                    data['additional_brand'] = word.capitalize()
                    break
    
    return data

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    flash('Logged out successfully', 'success')
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
