from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
import uuid
import psycopg2
from psycopg2.extras import RealDictCursor
import smtplib
from email.mime.text import MIMEText
from functools import wraps
import secrets
import ffmpeg
import librosa
import whisper
import re
import logging
import pytz
import soundfile as sf
import resampy
import magic
from dotenv import load_dotenv

load_dotenv()


# Load Whisper model once at startup (choose 'tiny' for performance)
whisper_model = whisper.load_model('tiny')

app = Flask(__name__)
csrf = CSRFProtect(app)

# Configuration settings

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change_me')
app.config['DB_HOST'] = os.environ.get('DB_HOST')
app.config['DB_NAME'] = os.environ.get('DB_NAME')
app.config['DB_USER'] = os.environ.get('DB_USER')
app.config['DB_PASSWORD'] = os.environ.get('DB_PASSWORD')
app.config['DB_PORT'] = os.environ.get('DB_PORT', '5432')
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', 'static/uploads')
app.config['ALLOWED_EXTENSIONS'] = set(os.environ.get('ALLOWED_EXTENSIONS', 'png,jpg,jpeg,mp4,mov,webm').split(','))
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))


# Email configuration settings
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL', 'False').lower() == 'true'
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database connection
from contextlib import closing

def get_db_connection():
    """Return a context manager for a PostgreSQL connection."""
    return closing(psycopg2.connect(
        host=app.config['DB_HOST'],
        database=app.config['DB_NAME'],
        user=app.config['DB_USER'],
        password=app.config['DB_PASSWORD'],
        port=app.config['DB_PORT']
    ))

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Decorator to check if user is logged in
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('You need to be logged in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Check if the uploaded file is allowed
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# Validate uploaded files using MIME sniffing and size checks
ALLOWED_IMAGE_MIME_TYPES = {'image/png', 'image/jpeg'}
ALLOWED_VIDEO_MIME_TYPES = {'video/mp4', 'video/quicktime', 'video/webm'}

def validate_upload(file, allowed_mime_types):
    if not file or file.filename == '':
        return False, 'No file selected'

    if not allowed_file(file.filename):
        return False, 'Invalid file extension'

    # Check actual file size
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > app.config['MAX_CONTENT_LENGTH']:
        return False, 'File exceeds size limit'

    # Sniff MIME type from content
    mime_type = magic.from_buffer(file.read(2048), mime=True)
    file.seek(0)
    if mime_type not in allowed_mime_types:
        return False, 'Invalid file MIME type'

    return True, None

# Mock brand and SKU data (replace with database query if needed)
def get_brands_and_skus(category):
    data = {
        'snack': {'brands': ['Pringles', 'Lay’s'], 'skus': ['50g', '100g']},
        'meal': {'brands': ['Jollof', 'Poundo'], 'skus': ['plate', 'bowl']},
        'beverage': {'brands': ['Coca-Cola', 'Pepsi', 'Fanta'], 'skus': ['500ml', '1l']},
        'other': {'brands': ['Generic'], 'skus': ['unit']}
    }
    return data.get(category, {'brands': [], 'skus': []})

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, password FROM users WHERE email = %s", (email,))
            user = cur.fetchone()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            flash('Logged in successfully!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password', 'danger')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        hashed_password = generate_password_hash(password)
        
        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute(
                    "INSERT INTO users (name, email, password) VALUES (%s, %s, %s)",
                    (name, email, hashed_password)
                )
                conn.commit()
                session['username'] = name  # Added for dashboard consistency
                flash('Registration successful! Please login.', 'success')
                return redirect(url_for('login'))
            except psycopg2.Error as e:
                conn.rollback()
                flash('Registration failed. Email may already be in use.', 'danger')
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    flash('Logged out successfully', 'success')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT name FROM users WHERE id = %s", (session['user_id'],))
        user_name = cur.fetchone()['name']
        cur.execute("""
            SELECT
                COUNT(*) as total_entries,
                COUNT(DISTINCT brand) as unique_brands,
                ROUND(AVG(amount_paid), 2) as avg_spending,
                COUNT(DISTINCT consume_location) as locations_visited
            FROM consumption_records
            WHERE user_id = %s
        """, (session['user_id'],))
        stats = cur.fetchone()
        cur.execute("""
            SELECT
                purchase_location,
                COUNT(*) as count
            FROM consumption_records
            WHERE user_id = %s
            GROUP BY purchase_location
            ORDER BY count DESC
            LIMIT 1
        """, (session['user_id'],))
        top_purchase_location = cur.fetchone()
        cur.execute("""
            SELECT
                id, product_category, brand, sku, amount_paid, purchase_location,
                consume_location, with_whom, to_char(submission_timestamp, 'YYYY-MM-DD HH24:MI') as date,
                CASE
                    WHEN additional_product_category IS NOT NULL THEN 'Yes'
                    ELSE 'No'
                END as had_additional_items
            FROM consumption_records
            WHERE user_id = %s
            ORDER BY submission_timestamp DESC
            LIMIT 5
        """, (session['user_id'],))
        recent_activities = cur.fetchall()
        cur.execute("""
            SELECT
                product_category,
                COUNT(*) as count,
                SUM(amount_paid) as total_spent
            FROM consumption_records
            WHERE user_id = %s
            GROUP BY product_category
        """, (session['user_id'],))
        consumption_by_category_raw = cur.fetchall()
        consumption_by_category = [
            {
                'product_category': row['product_category'],
                'count': row['count'],
                'total_spent': row['total_spent'] if row['total_spent'] is not None else 0
            }
            for row in consumption_by_category_raw
        ]
    return render_template(
        'dashboard.html',
        user_name=user_name,
        total_entries=stats['total_entries'],
        unique_brands=stats['unique_brands'],
        avg_spending=stats['avg_spending'],
        locations_visited=stats['locations_visited'],
        top_purchase_location=top_purchase_location['purchase_location'] if top_purchase_location else 'N/A',
        recent_activities=recent_activities,
        consumption_by_category=consumption_by_category
    )

@app.route('/track')
@login_required
def track():
    return render_template('track.html')

@app.route('/instructions')
@login_required
def instructions():
    return render_template('instructions.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        
        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            user = cur.fetchone()

            if user:
                token = secrets.token_urlsafe(32)
                expires = datetime.now() + timedelta(hours=1)

                cur.execute("""
                    UPDATE users
                    SET reset_token = %s, reset_token_expires = %s
                    WHERE email = %s
                """, (token, expires, email))
                conn.commit()

                reset_link = url_for('reset_password', token=token, _external=True)
                send_password_reset_email(email, reset_link)

            flash('If an account exists with this email, you will receive a password reset link', 'info')
        return redirect(url_for('login'))
    
    return render_template('forgot_password.html')

def send_password_reset_email(email, reset_link):
    msg = MIMEText(f"""
    <html>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #333;">
            <div style="max-width: 600px; margin: auto; padding: 20px; background: #f8f9fa; border-radius: 8px;">
                <img src="https://ooh-tracker.onrender.com/static/logo.jpg" alt="OOH Tracker Logo" style="display: block; margin: 0 auto; width: 100px;">
                <h2 style="text-align: center; color: #4e73df;">Password Reset Request</h2>
                <p>Hello,</p>
                <p>You requested a password reset for your OOH Tracker account. Click the button below to reset your password:</p>
                <p style="text-align: center;">
                    <a href="{reset_link}" style="display: inline-block; padding: 10px 20px; background: #4e73df; color: white; text-decoration: none; border-radius: 4px;">Reset Password</a>
                </p>
                <p>This link will expire in 1 hour. If you didn’t request this, please ignore this email.</p>
                <p>Best regards,<br>The OOH Tracker Team</p>
            </div>
        </body>
    </html>
    """, 'html')
    
    msg['Subject'] = 'Password Reset Request - OOH Tracker'
    msg['To'] = email
    
    with smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT']) as server:
        server.starttls()
        server.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
        server.send_message(msg)

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, email, reset_token_expires
            FROM users
            WHERE reset_token = %s AND reset_token_expires > CURRENT_TIMESTAMP
        """, (token,))
        user = cur.fetchone()

        if not user:
            flash('Invalid or expired token', 'danger')
            return redirect(url_for('forgot_password'))

        if request.method == 'POST':
            password = request.form['password']
            confirm_password = request.form['confirm_password']

            if password != confirm_password:
                flash('Passwords do not match', 'danger')
                return redirect(request.url)

            hashed_password = generate_password_hash(password)
            cur.execute("""
                UPDATE users
                SET password = %s, reset_token = NULL, reset_token_expires = NULL
                WHERE id = %s
            """, (hashed_password, user['id']))
            conn.commit()

            flash('Your password has been updated. Please login with your new password.', 'success')
            return redirect(url_for('login'))

        return render_template('reset_password.html', token=token)

@app.route('/api/get-brands-and-skus')
@login_required
def get_brands_and_skus_route():
    category = request.args.get('product_category')
    data = get_brands_and_skus(category)
    return jsonify({'brands': data['brands'], 'skus': data['skus']})

@app.route('/api/submit-consumption', methods=['POST'])
@login_required
def submit_consumption():
    try:
        product_category = request.form.get('product_category')
        brand = request.form.get('brand')
        sku = request.form.get('sku')
        amount_paid = request.form.get('amount_paid')
        purchase_location = request.form.get('purchase_location')
        consume_location = request.form.get('consume_location')
        with_whom = request.form.get('with_whom')
        with_what = request.form.get('with_what')
        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')
        accuracy = request.form.get('accuracy')
        additional_product_category = request.form.get('additional_product_category')
        additional_brand = request.form.get('additional_brand')
        additional_sku = request.form.get('additional_sku')
        additional_amount_paid = request.form.get('additional_amount_paid')
        additional_purchase_location = request.form.get('additional_purchase_location')
        submission_timestamp = request.form.get('submission_timestamp')

        photo_path = None
        video_path = None

        if 'photo' in request.files:
            photo = request.files['photo']
            valid, error = validate_upload(photo, ALLOWED_IMAGE_MIME_TYPES)
            if not valid:
                return jsonify({'success': False, 'message': error}), 400
            filename = secure_filename(f"{uuid.uuid4()}_{photo.filename}")
            photo_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            photo.save(photo_path)
            photo_path_db = f"uploads/{filename}"

        if 'video' in request.files:
            video = request.files['video']
            valid, error = validate_upload(video, ALLOWED_VIDEO_MIME_TYPES)
            if not valid:
                return jsonify({'success': False, 'message': error}), 400
            filename = secure_filename(f"{uuid.uuid4()}_{video.filename}")
            video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            video.save(video_path)
            video_path_db = f"uploads/{filename}"

        required_fields = {
            'product_category': product_category,
            'brand': brand,
            'sku': sku,
            'amount_paid': amount_paid,
            'purchase_location': purchase_location,
            'consume_location': consume_location,
            'with_whom': with_whom,
            'with_what': with_what
        }
        for field_name, field_value in required_fields.items():
            if not field_value:
                return jsonify({'success': False, 'message': f'Missing required field: {field_name}'}), 400

        # Parse submission_timestamp
        try:
            timestamp = datetime.strptime(submission_timestamp, '%Y-%m-%d %H:%M:%S %Z') if submission_timestamp else None
            if timestamp:
                wat = pytz.timezone('Africa/Lagos')
                timestamp = wat.localize(timestamp)
        except ValueError:
            timestamp = None
            logger.warning("Invalid submission_timestamp format; using NULL")

        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = """
                INSERT INTO consumption_records (
                    user_id, product_category, brand, sku, amount_paid, purchase_location,
                    consume_location, with_whom, with_what, latitude, longitude, accuracy,
                    additional_product_category, additional_brand, additional_sku,
                    additional_amount_paid, additional_purchase_location,
                    photo_path, video_path, submission_timestamp
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """
            values = (
                session['user_id'], product_category, brand, sku, float(amount_paid), purchase_location,
                consume_location, with_whom, with_what, float(latitude) if latitude else None,
                float(longitude) if longitude else None, float(accuracy) if accuracy else None,
                additional_product_category, additional_brand, additional_sku,
                float(additional_amount_paid) if additional_amount_paid else None,
                additional_purchase_location, photo_path_db if photo_path else None,
                video_path_db if video_path else None, timestamp
            )

            cur.execute(query, values)
            record_id = cur.fetchone()['id']
            conn.commit()

            return jsonify({
                'success': True,
                'message': 'Consumption recorded successfully',
                'record_id': record_id
            })

    except psycopg2.Error as e:
        logger.error(f"Error submitting consumption: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@app.route('/api/analyze-video', methods=['POST'])
@login_required
def analyze_video():
    if 'video' not in request.files:
        return jsonify({'success': False, 'message': 'No video file provided'}), 400

    file = request.files['video']
    valid, error = validate_upload(file, ALLOWED_VIDEO_MIME_TYPES)
    if not valid:
        return jsonify({'success': False, 'message': error}), 400

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = secure_filename(f"user_{session['user_id']}_{timestamp}.{file.filename.rsplit('.', 1)[1].lower()}")
    video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(video_path)

    audio_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{timestamp}.wav")

    try:
        # Check video duration (limit: 60 seconds)
        probe = ffmpeg.probe(video_path)
        duration = float(probe['format']['duration'])
        if duration > 60:
            return jsonify({'success': False, 'message': 'Video exceeds 1-minute limit'}), 400

        # Extract audio (mono, 16kHz, PCM WAV)
        stream = ffmpeg.input(video_path)
        stream = ffmpeg.output(stream, audio_path, acodec='pcm_s16le', ar=16000, ac=1, vn=True, format='wav', loglevel='error')
        ffmpeg.run(stream, overwrite_output=True)

        if os.path.getsize(audio_path) > 10 * 1024 * 1024:
            return jsonify({'success': False, 'message': 'Extracted audio exceeds 10MB limit'}), 400

        # Load and preprocess audio
        y, sr = sf.read(audio_path)

        if len(y.shape) > 1:
            y = y.mean(axis=1)  # Convert to mono

        if sr != 16000:
            y = resampy.resample(y, sr, 16000)
            sr = 16000

        y, _ = librosa.effects.trim(y)
        sf.write(audio_path, y, sr)

        # Transcribe audio
        result = whisper_model.transcribe(audio_path)
        transcript = result['text'].lower()

        # Parse transcript
        data = parse_transcript(transcript)
        data['video_path'] = f"uploads/{filename}"
        data['submission_timestamp'] = request.form.get('submission_timestamp', None)

        return jsonify({'success': True, 'data': data})

    except ffmpeg.Error as e:
        logger.error(f"FFmpeg error: {e.stderr.decode()}")
        return jsonify({'success': False, 'message': f'FFmpeg error: {e.stderr.decode()}'}), 500
    except Exception as e:
        logger.error(f"Error analyzing video: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


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
        'additional_product_category': None,
        'additional_brand': None,
        'additional_sku': None,
        'additional_amount_paid': None,
        'additional_purchase_location': None
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
    
    # additional fields
    if data['with_what'] == 'yes':
        for brand in all_brands:
            if brand.lower() in transcript and brand != data['brand']:
                data['additional_brand'] = brand
                for category, cat_data in category_keywords.items():
                    if brand in get_brands_and_skus(category)['brands']:
                        data['additional_product_category'] = category
                        break
                break
        if not data['additional_brand']:
            words = transcript.split()
            for i, word in enumerate(words):
                if word in [data['brand'].lower()] if data['brand'] else []:
                    continue
                if word in all_brands or len(word) > 3:
                    data['additional_brand'] = word.capitalize()
                    break
        # additional_sku
        if sku_match and data['sku']:
            words = transcript.split()
            for i, word in enumerate(words):
                if word == data['sku'].lower():
                    continue
                sku_match = re.search(r'(\d+\.?\d*)\s*(ml|l|liter|kg|g|gram|ounce|oz|pack|large|medium|small)', ' '.join(words[i:]))
                if sku_match:
                    sku_value = f"{sku_match.group(1)}{sku_match.group(2)}"
                    if sku_value in all_skus:
                        data['additional_sku'] = sku_value
                        break
        # additional_amount_paid
        amount_matches = re.findall(r'(\d+\.?\d*)\s*(naira|₦)', transcript)
        if len(amount_matches) > 1:
            data['additional_amount_paid'] = float(amount_matches[1][0])
        # additional_purchase_location
        for location, keywords in purchase_keywords.items():
            if any(keyword in transcript for keyword in keywords) and location != data['purchase_location']:
                data['additional_purchase_location'] = location
                break
    
    return data

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    response = send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    response.headers['Cache-Control'] = 'public, max-age=31536000'  # Cache for 1 year
    return response

if __name__ == '__main__':
    app.run(debug=True)
