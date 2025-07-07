from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from flask_mysqldb import MySQL
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
import uuid
import json
from functools import wraps
import secrets
import smtplib
from email.mime.text import MIMEText

app = Flask(__name__)

# Configuration settings
app.config['SECRET_KEY'] = 'your_secret_key'  # Change this to a random secret key
app.config['MYSQL_HOST'] = 'sql8.freesqldatabase.com'
app.config['MYSQL_USER'] = 'sql8786924'
app.config['MYSQL_PASSWORD'] = 'kBXsWnbhwA'
app.config['MYSQL_DB'] = 'sql8786924'
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
app.config['MAIL_DEFAULT_SENDER'] = 'seun080ade@gmail.com'  # Replace with your Gmail address

mysql = MySQL(app)
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
        
        cur = mysql.connection.cursor()
        cur.execute("SELECT id, password FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()
        
        if user and check_password_hash(user[1], password):
            session['user_id'] = user[0]
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
        
        cur = mysql.connection.cursor()
        try:
            cur.execute(
                "INSERT INTO users (name, email, password) VALUES (%s, %s, %s)",
                (name, email, hashed_password)
            )
            mysql.connection.commit()
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            mysql.connection.rollback()
            flash('Registration failed. Please try again.', 'danger')
        finally:
            cur.close()
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
    cur = mysql.connection.cursor()
    cur.execute("SELECT name FROM users WHERE id = %s", (session['user_id'],))
    user_name = cur.fetchone()[0]
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
            amount_paid, purchase_location, consume_location, with_whom, DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i') as date,
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
    # Convert tuples to dictionaries
    consumption_by_category = [
        {
            'product_category': row[0],
            'count': row[1],
            'total_spent': row[2] if row[2] is not None else 0
        }
        for row in consumption_by_category_raw
    ]
    cur.close()
    return render_template(
        'dashboard.html',
        user_name=user_name,
        total_entries=stats[0],
        unique_brands=stats[1],
        avg_spending=stats[2],
        locations_visited=stats[3],
        top_purchase_location=top_purchase_location[0] if top_purchase_location else 'N/A',
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
        
        cur = mysql.connection.cursor()
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        
        if user:
            # Generate token
            token = secrets.token_urlsafe(32)
            expires = datetime.now() + timedelta(hours=1)
            
            # Store token in database
            cur.execute("""
                UPDATE users 
                SET reset_token = %s, reset_token_expires = %s 
                WHERE email = %s
            """, (token, expires, email))
            mysql.connection.commit()
            
            # Send email
            reset_link = url_for('reset_password', token=token, _external=True)
            send_password_reset_email(email, reset_link)
            
        # Always show success message (even if email doesn't exist)
        flash('If an account exists with this email, you will receive a password reset link', 'info')
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
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT id, email, reset_token_expires 
        FROM users 
        WHERE reset_token = %s AND reset_token_expires > NOW()
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
        
        # Update password and clear token
        hashed_password = generate_password_hash(password)
        cur.execute("""
            UPDATE users 
            SET password = %s, reset_token = NULL, reset_token_expires = NULL 
            WHERE id = %s
        """, (hashed_password, user[0]))
        mysql.connection.commit()
        cur.close()
        
        flash('Your password has been updated. Please login with your new password.', 'success')
        return redirect(url_for('login'))
    
    return render_template('reset_password.html', token=token)


@app.route('/api/submit-consumption-mysql', methods=['POST'])
@login_required
def submit_consumption():
    try:
        # Extract form data
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

        # Handle additional product info (optional fields)
        additional_product_category = request.form.get('additional_product_category', None)
        additional_brand = request.form.get('additional_brand', None)
        additional_sku = request.form.get('additional_sku', None)
        additional_amount_paid = request.form.get('additional_amount_paid', None)
        additional_purchase_location = request.form.get('additional_purchase_location', None)

        # Handle file uploads
        photo_path = None
        video_path = None
        audio_path = None

        # Process photo upload
        if 'photo' in request.files:
            photo = request.files['photo']
            if photo.filename and allowed_file(photo.filename):
                filename = secure_filename(f"{uuid.uuid4()}_{photo.filename}")
                photo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                photo_path = f"uploads/{filename}"

        # Process video upload
        if 'video' in request.files:
            video = request.files['video']
            if video.filename and allowed_file(video.filename):
                filename = secure_filename(f"{uuid.uuid4()}_{video.filename}")
                video.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                video_path = f"uploads/{filename}"

        # Process audio upload
        if 'audio' in request.files:
            audio = request.files['audio']
            if audio.filename and allowed_file(audio.filename):
                filename = secure_filename(f"{uuid.uuid4()}_{audio.filename}")
                audio.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                audio_path = f"uploads/{filename}"

        # Validate required fields
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

        # Insert data into MySQL
        cur = mysql.connection.cursor()
        query = """
            INSERT INTO consumption_records (
                user_id, product_category, brand, sku, amount_paid, purchase_location,
                consume_location, with_whom, with_what, latitude, longitude, accuracy,
                additional_product_category, additional_brand, additional_sku,
                additional_amount_paid, additional_purchase_location,
                photo_path, video_path, audio_path
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        values = (
            session['user_id'], product_category, brand, sku, float(amount_paid), purchase_location,
            consume_location, with_whom, with_what, float(latitude) if latitude else None,
            float(longitude) if longitude else None, float(accuracy) if accuracy else None,
            additional_product_category, additional_brand, additional_sku,
            float(additional_amount_paid) if additional_amount_paid else None,
            additional_purchase_location, photo_path, video_path, audio_path
        )

        cur.execute(query, values)
        mysql.connection.commit()
        cur.close()

        return jsonify({'success': True, 'message': 'Consumption recorded successfully'})

    except Exception as e:
        mysql.connection.rollback()
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True)
