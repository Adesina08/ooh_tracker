from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from flask_mysqldb import MySQL
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os
import uuid
import json
from functools import wraps
from datetime import datetime, timedelta
import secrets
import smtplib
from email.mime.text import MIMEText

app = Flask(__name__)

# Configuration settings
db=mysql.connector.connect(
    host=os.getenv("DB_HOST"),
    user=os.getenv("DB_USERNAME"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME"),
    port=int(os.getenv("DB_PORT", 3306))
)

app.config['DB_DB'] = 'ooh_tracker'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'mp4', 'mov'}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size

mysql = MySQL(app)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
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
    # Get user's name
    cur = mysql.connection.cursor()
    cur.execute("SELECT name FROM users WHERE id = %s", (session['user_id'],))
    user_name = cur.fetchone()[0]
    
    # Get basic statistics
    cur.execute("""
        SELECT 
            COUNT(*) as total_entries,
            COUNT(DISTINCT brand) as unique_brands,
            ROUND(AVG(satisfaction), 1) as avg_rating
        FROM consumption_records
        WHERE user_id = %s
    """, (session['user_id'],))
    stats = cur.fetchone()
    
    # Get most frequent location
    cur.execute("""
        SELECT 
            location_type,
            COUNT(*) as count
        FROM consumption_records
        WHERE user_id = %s AND location_type IS NOT NULL
        GROUP BY location_type
        ORDER BY count DESC
        LIMIT 1
    """, (session['user_id'],))
    top_location = cur.fetchone()
    
    # Get recent activities
    cur.execute("""
        SELECT 
            brand, 
            food_type, 
            location_type,
            price,
            satisfaction,
            DATE_FORMAT(timestamp, '%%Y-%%m-%%d') as date
        FROM consumption_records
        WHERE user_id = %s
        ORDER BY timestamp DESC
        LIMIT 5
    """, (session['user_id'],))
    recent_activities = cur.fetchall()
    
    cur.close()
    
    return render_template(
        'dashboard.html',
        user_name=user_name,
        total_entries=stats[0],
        unique_brands=stats[1],
        avg_rating=stats[2],
        top_location=top_location[0] if top_location else 'N/A',
        recent_activities=recent_activities
    )


@app.route('/track')
@login_required
def track():
    return render_template('track.html')

@app.route('/instructions')
@login_required
def instructions():
    return render_template('instructions.html')

# Add these new routes
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
    You requested a password reset for your OOH Tracker account.
    
    Please click the following link to reset your password:
    {reset_link}
    
    This link will expire in 1 hour.
    
    If you didn't request this, please ignore this email.
    """)
    
    msg['Subject'] = 'Password Reset Request'
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

if __name__ == '__main__':
    app.run(debug=True)
