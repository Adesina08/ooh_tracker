from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
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

app = Flask(__name__)

# Configuration settings
app.config['SECRET_KEY'] = 'your_secret_key'  # Change this to a random secret key
app.config['DB_HOST'] = 'dpg-d1m16indiees7389jq50-a.oregon-postgres.render.com'
app.config['DB_NAME'] = 'ooh_tracker_db'
app.config['DB_USER'] = 'ooh_tracker_db_user'
app.config['DB_PASSWORD'] = 'bZvhR8NpLOxIXQRnSC7qt6tn9Ny7T6jf'
app.config['DB_PORT'] = '5432'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'mp4', 'mov', 'mp3', 'wav', 'm4a'}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size

# Email configuration settings
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USERNAME'] = 'seun080ade@gmail.com'  # Replace with your Gmail address
app.config['MAIL_PASSWORD'] = 'fjfs tlnw smgs rrf'  # Replace with your Gmail App Password
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_DEFAULT_SENDER'] = 'seun080ade@gmail.com'

# Database connection
def get_db_connection():
    conn = psycopg2.connect(
        host=app.config['DB_HOST'],
        database=app.config['DB_NAME'],
        user=app.config['DB_USER'],
        password=app.config['DB_PASSWORD'],
        port=app.config['DB_PORT']
    )
    return conn

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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, password FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
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
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO users (name, email, password) VALUES (%s, %s, %s)",
                (name, email, hashed_password)
            )
            conn.commit()
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))
        except psycopg2.Error as e:
            conn.rollback()
            flash('Registration failed. Email may already be in use.', 'danger')
        finally:
            cur.close()
            conn.close()
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    session.pop('user_id', None)
    flash('Logged out successfully', 'success')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
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
            consume_location, with_whom, to_char(created_at, 'YYYY-MM-DD HH24:MI') as date,
            CASE 
                WHEN additional_product_category IS NOT NULL THEN 'Yes'
                ELSE 'No'
            END as had_additional_items
        FROM consumption_records
        WHERE user_id = %s
        ORDER BY created_at DESC
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
    cur.close()
    conn.close()
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
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
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
        cur.close()
        conn.close()
        return redirect(url_for('login'))
    
    return render_template('forgot_password.html')

def send_password_reset_email(email, reset_link):
    msg = MIMEText(f"""
    <html>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #333;">
            <div style="max-width: 600px; margin: auto; padding: 20px; background: #f8f9fa; border-radius: 8px;">
                <img src="http://127.0.0.1:5000/static/logo.jpg" alt="OOH Tracker Logo" style="display: block; margin: 0 auto; width: 100px;">
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
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT id, email, reset_token_expires 
        FROM users 
        WHERE reset_token = %s AND reset_token_expires > CURRENT_TIMESTAMP
    """, (token,))
    user = cur.fetchone()
    
    if not user:
        flash('Invalid or expired token', 'danger')
        cur.close()
        conn.close()
        return redirect(url_for('forgot_password'))
    
    if request.method == 'POST':
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        
        if password != confirm_password:
            flash('Passwords do not match', 'danger')
            cur.close()
            conn.close()
            return redirect(request.url)
        
        hashed_password = generate_password_hash(password)
        cur.execute("""
            UPDATE users 
            SET password = %s, reset_token = NULL, reset_token_expires = NULL 
            WHERE id = %s
        """, (hashed_password, user['id']))
        conn.commit()
        cur.close()
        conn.close()
        
        flash('Your password has been updated. Please login with your new password.', 'success')
        return redirect(url_for('login'))
    
    cur.close()
    conn.close()
    return render_template('reset_password.html', token=token)

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

        photo_path = None
        video_path = None
        audio_path = None

        if 'photo' in request.files:
            photo = request.files['photo']
            if photo.filename and allowed_file(photo.filename):
                filename = secure_filename(f"{uuid.uuid4()}_{photo.filename}")
                photo_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                photo.save(photo_path)
                photo_path_db = f"uploads/{filename}"

        if 'video' in request.files:
            video = request.files['video']
            if video.filename and allowed_file(video.filename):
                filename = secure_filename(f"{uuid.uuid4()}_{video.filename}")
                video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                video.save(video_path)
                video_path_db = f"uploads/{filename}"

        if 'audio' in request.files:
            audio = request.files['audio']
            if audio.filename and allowed_file(audio.filename):
                filename = secure_filename(f"{uuid.uuid4()}_{audio.filename}")
                audio_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                audio.save(audio_path)
                audio_path_db = f"uploads/{filename}"

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

        conn = get_db_connection()
        cur = conn.cursor()
        query = """
            INSERT INTO consumption_records (
                user_id, product_category, brand, sku, amount_paid, purchase_location,
                consume_location, with_whom, with_what, latitude, longitude, accuracy,
                additional_product_category, additional_brand, additional_sku,
                additional_amount_paid, additional_purchase_location,
                photo_path, video_path, audio_path
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
            video_path_db if video_path else None, audio_path_db if audio_path else None
        )

        cur.execute(query, values)
        record_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': 'Consumption recorded successfully',
            'record_id': record_id
        })

    except psycopg2.Error as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True)
