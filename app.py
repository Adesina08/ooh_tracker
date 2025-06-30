from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_mysqldb import MySQL
from flask_wtf import FlaskForm
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import smtplib
from email.mime.text import MIMEText
import os
import json
import logging
import whisper
import moviepy.editor as mp
import spacy
from functools import wraps
from datetime import datetime, timedelta
import uuid
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configuration settings from .env
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default_secret_key')  # Fallback for development
app.config['MYSQL_HOST'] = os.getenv('MYSQL_HOST')
app.config['MYSQL_USER'] = os.getenv('MYSQL_USER')
app.config['MYSQL_PASSWORD'] = os.getenv('MYSQL_PASSWORD')
app.config['MYSQL_DB'] = os.getenv('MYSQL_DB')
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'mp4', 'mov', 'mp3', 'wav', 'm4a'}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size

# Email configuration settings
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')

mysql = MySQL(app)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Configure logging
logging.basicConfig(filename='app.log', level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Load Whisper and SpaCy
cache_dir = os.path.expanduser("~/.cache/whisper")
os.makedirs(cache_dir, exist_ok=True)
model_path = os.path.join(cache_dir, "base.pt")
if os.path.exists(model_path):
    whisper_model = whisper.load_model("base", download_root=cache_dir)
    logging.info("Using cached Whisper model from %s", model_path)
else:
    try:
        whisper_model = whisper.load_model("base", download_root=cache_dir)
        logging.info("Downloaded and loaded Whisper model 'base'")
    except Exception as e:
        logging.error("Failed to load Whisper model: %s", str(e))
        raise Exception("Whisper model download failed. Please ensure an internet connection and sufficient disk space.")

# Load SpaCy model (e.g., 'en_core_web_sm' for small English model)
try:
    nlp = spacy.load("en_core_web_sm")
    logging.info("Loaded SpaCy model 'en_core_web_sm'")
except OSError:
    os.system("python -m spacy download en_core_web_sm")
    nlp = spacy.load("en_core_web_sm")
    logging.info("Downloaded and loaded SpaCy model 'en_core_web_sm'")

# Load static categories and brands
try:
    with open(os.path.join(app.static_folder, 'categories_brands.json'), 'r') as f:
        static_data = json.load(f)
except FileNotFoundError:
    logging.error("categories_brands.json not found in static folder")
    static_data = {"categories": {}}

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, email, name=None, is_admin=False):
        self.id = id
        self.email = email
        self.name = name
        self.is_admin = is_admin

@login_manager.user_loader
def load_user(user_id):
    cur = mysql.connection.cursor()
    cur.execute("SELECT id, email, name, is_admin FROM users WHERE id = %s", (user_id,))
    user_data = cur.fetchone()
    cur.close()
    if user_data:
        return User(user_data[0], user_data[1], user_data[2], bool(user_data[3]))
    return None

# Forms
class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class RegisterForm(FlaskForm):
    name = StringField('Full Name', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), EqualTo('confirm_password', message='Passwords must match')])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired()])
    submit = SubmitField('Register')

# Login required decorator with admin check
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Access denied. Admins only.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data
        password = form.password.data
        cur = mysql.connection.cursor()
        cur.execute("SELECT id, email, password, is_admin FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        if user and check_password_hash(user[2], password):
            cur.execute("SELECT name FROM users WHERE id = %s", (user[0],))
            name = cur.fetchone()[0]
            user_obj = User(user[0], user[1], name, bool(user[3]))
            login_user(user_obj)
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard' if not user_obj.is_admin else 'admin_dashboard'))
        else:
            flash('Email not found or invalid password.', 'danger')
        cur.close()
    return render_template('login.html', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        name = form.name.data
        email = form.email.data
        password = form.password.data
        hashed_password = generate_password_hash(password)
        try:
            cur = mysql.connection.cursor()
            cur.execute("SELECT email FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                flash('Email already exists.', 'danger')
                cur.close()
                return render_template('register.html', form=form)
            cur.execute("INSERT INTO users (name, email, password, is_admin, created_at, updated_at) VALUES (%s, %s, %s, %s, NOW(), NOW())", (name, email, hashed_password, False))
            mysql.connection.commit()
            cur.close()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            logging.error(f"Registration failed: {str(e)}")
            flash('Registration failed due to an internal error.', 'danger')
            if 'cur' in locals():
                cur.close()
    return render_template('register.html', form=form)

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        cur = mysql.connection.cursor()
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        if user:
            token = str(uuid.uuid4())
            expires_at = datetime.now() + timedelta(hours=1)
            cur.execute("UPDATE users SET reset_token = %s, reset_token_expires = %s WHERE id = %s", (token, expires_at, user[0]))
            mysql.connection.commit()
            reset_link = url_for('reset_password', token=token, _external=True)
            try:
                send_password_reset_email(email, reset_link)
                flash('If an account exists with this email, you will receive a password reset link.', 'info')
            except Exception as e:
                logging.error(f"Failed to send reset email: {str(e)}")
                flash('Error sending reset email. Please try again later.', 'danger')
        else:
            flash('If an account exists with this email, you will receive a password reset link.', 'info')
        cur.close()
    return render_template('forgot_password.html')

def send_password_reset_email(email, reset_link):
    try:
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
    except Exception as e:
        logging.error(f"Failed to send password reset email to {email}: {str(e)}")
        raise

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    cur = mysql.connection.cursor()
    cur.execute("SELECT id FROM users WHERE reset_token = %s AND reset_token_expires > %s", (token, datetime.now()))
    user = cur.fetchone()
    if not user:
        cur.close()
        flash('Invalid or expired reset link.', 'danger')
        return redirect(url_for('login'))
    if request.method == 'POST':
        password = request.form['password']
        hashed_password = generate_password_hash(password)
        cur.execute("UPDATE users SET password = %s, reset_token = NULL, reset_token_expires = NULL, updated_at = NOW() WHERE id = %s", (hashed_password, user[0]))
        mysql.connection.commit()
        cur.close()
        flash('Password reset successfully! Please log in.', 'success')
        return redirect(url_for('login'))
    cur.close()
    return render_template('reset_password.html', token=token)

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    user_id = current_user.id
    try:
        cur = mysql.connection.cursor()
        cur.execute("SELECT COUNT(*) FROM consumption_records WHERE user_id = %s", (user_id,))
        total_entries = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT brand) FROM consumption_records WHERE user_id = %s", (user_id,))
        unique_brands = cur.fetchone()[0]
        cur.execute("SELECT AVG(amount_paid) FROM consumption_records WHERE user_id = %s", (user_id,))
        avg_spending = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT purchase_location) FROM consumption_records WHERE user_id = %s", (user_id,))
        locations_visited = cur.fetchone()[0]
        cur.execute("SELECT purchase_location, COUNT(*) as count FROM consumption_records WHERE user_id = %s GROUP BY purchase_location ORDER BY count DESC LIMIT 1", (user_id,))
        top_purchase_location = cur.fetchone()[0] if cur.rowcount > 0 else None
        cur.execute("SELECT * FROM consumption_records WHERE user_id = %s ORDER BY created_at DESC LIMIT 5", (user_id,))
        recent_activities = cur.fetchall()
        cur.execute("SELECT product_category, COUNT(*) as count, SUM(amount_paid) as total_spent FROM consumption_records WHERE user_id = %s GROUP BY product_category", (user_id,))
        consumption_by_category = cur.fetchall()
        cur.close()
        return render_template('dashboard.html', user_name=current_user.email, total_entries=total_entries, unique_brands=unique_brands,
                              avg_spending=avg_spending, locations_visited=locations_visited, top_purchase_location=top_purchase_location,
                              recent_activities=recent_activities, consumption_by_category=consumption_by_category)
    except Exception as e:
        logging.error(f"Dashboard error: {str(e)}")
        flash('Error loading dashboard data.', 'danger')
        return render_template('dashboard.html', user_name=current_user.email, total_entries=0, unique_brands=0,
                              avg_spending=0, locations_visited=0, top_purchase_location=None,
                              recent_activities=[], consumption_by_category=[])

@app.route('/track')
@login_required
def track():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    return render_template('track.html')

@app.route('/api/categories', methods=['GET'])
@login_required
def get_categories():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    try:
        cur = mysql.connection.cursor()
        cur.execute("SELECT DISTINCT product_category FROM consumption_records WHERE user_id = %s", (current_user.id,))
        db_categories = [row[0] for row in cur.fetchall() if row[0]]
        cur.close()
        static_categories = list(static_data['categories'].keys())
        combined_categories = sorted(set(static_categories + db_categories))
        return jsonify(combined_categories)
    except Exception as e:
        logging.error(f"Error fetching categories: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/brands/<category>', methods=['GET'])
@login_required
def get_brands(category):
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    try:
        cur = mysql.connection.cursor()
        cur.execute("SELECT DISTINCT brand FROM consumption_records WHERE user_id = %s AND product_category = %s", (current_user.id, category))
        db_brands = [row[0] for row in cur.fetchall() if row[0]]
        cur.close()
        static_brands = static_data['categories'].get(category, [])
        combined_brands = sorted(set(static_brands + db_brands))
        return jsonify(combined_brands)
    except Exception as e:
        logging.error(f"Error fetching brands for {category}: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/submit-consumption-mysql', methods=['POST'])
@login_required
def submit_consumption():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    try:
        data = request.form
        user_id = current_user.id
        capture_type = data.get('capture_type', 'manual')
        logging.debug(f"Received submission: capture_type={capture_type}, form_data={dict(data)}")

        # Initialize fields
        product_category = data.get('product_category', '').strip()
        if product_category == 'Other':
            product_category = data.get('other_category', '').strip()
        brand = data.get('brand', '').strip()
        if brand == 'Other':
            brand = data.get('other_brand', '').strip()
        sku = data.get('sku', '').strip()
        amount_paid = float(data.get('amount_paid', 0)) if data.get('amount_paid') else 0
        purchase_location = data.get('purchase_location', '').strip()
        consume_location = data.get('consume_location', '').strip()
        with_whom = data.get('with_whom', '').strip()
        with_what = data.get('with_what', '').strip()
        additional_product_category = data.get('additional_product_category', '').strip() if with_what else ''
        additional_brand = data.get('additional_brand', '').strip() if with_what else ''
        additional_sku = data.get('additional_sku', '').strip() if with_what else ''
        latitude = float(data.get('latitude', 0)) if data.get('latitude') else None
        longitude = float(data.get('longitude', 0)) if data.get('longitude') else None

        # Validate required fields for Manual Capture
        if capture_type == 'manual':
            if not product_category or not brand or not amount_paid or not purchase_location or not consume_location:
                logging.error("Missing required fields in manual capture")
                return jsonify({"error": "All required fields must be filled."}), 400

        # Handle file upload
        media_path = None
        if 'media' in request.files and request.files['media'].filename:
            file = request.files['media']
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            media_path = file_path

            # AI Capture: Extract and analyze audio
            if capture_type == 'ai':
                try:
                    # Extract audio from video
                    video = mp.VideoFileClip(file_path)
                    audio_path = file_path.replace('.mp4', '.wav')
                    video.audio.write_audiofile(audio_path)
                    video.close()

                    # Transcribe audio with Whisper
                    result = whisper_model.transcribe(audio_path, language="en")
                    text = result["text"].lower()
                    logging.debug(f"Transcribed audio: {text}")

                    # Process text with SpaCy
                    doc = nlp(text)
                    field_values = {
                        'product_category': product_category,
                        'brand': brand,
                        'sku': sku,
                        'amount_paid': str(amount_paid),
                        'purchase_location': purchase_location,
                        'consume_location': consume_location,
                        'with_whom': with_whom,
                        'with_what': with_what
                    }
                    missing_fields = []

                    # Define known entities
                    known_categories = list(static_data['categories'].keys()) + ['beverage', 'snack', 'fast food']
                    known_brands = []
                    for cat in static_data['categories'].values():
                        known_brands.extend(cat)
                    known_brands = list(set(known_brands + ['coca-cola', 'pepsi', 'fanta', 'sprite', 'lay\'s', 'pringles', 'doritos', 'mcdonald\'s', 'kfc', 'burger king']))
                    known_locations = ['shoprite', 'lagos', 'abuja', 'market', 'store', 'restaurant', 'home', 'office', 'park']
                    known_companions = ['alone', 'friend', 'family', 'colleague']
                    known_with_what = ['none', 'food', 'drink', 'snack']

                    # Extract entities with SpaCy
                    for ent in doc.ents:
                        if not product_category and ent.label_ == "PRODUCT" and ent.text.lower() in [c.lower() for c in known_categories]:
                            product_category = ent.text.capitalize()
                            field_values['product_category'] = product_category
                        if not brand and ent.label_ == "ORG" and ent.text.lower() in [b.lower() for b in known_brands]:
                            brand = ent.text.capitalize()
                            field_values['brand'] = brand
                        if not sku and ent.label_ == "PRODUCT" and any(kw in ent.text.lower() for kw in ['sku', 'product id', 'item code']):
                            sku = ent.text
                            field_values['sku'] = sku
                        if not purchase_location and ent.label_ == "GPE" and ent.text.lower() in [l.lower() for l in known_locations]:
                            purchase_location = ent.text.capitalize()
                            field_values['purchase_location'] = purchase_location
                        if not consume_location and ent.label_ == "GPE" and ent.text.lower() in [l.lower() for l in known_locations]:
                            consume_location = ent.text.capitalize()
                            field_values['consume_location'] = consume_location
                        if not with_whom and ent.label_ == "PERSON" and ent.text.lower() in [c.lower() for c in known_companions]:
                            with_whom = ent.text.capitalize()
                            field_values['with_whom'] = with_whom
                        if not with_what and ent.label_ == "PRODUCT" and ent.text.lower() in [w.lower() for w in known_with_what]:
                            with_what = ent.text.capitalize()
                            field_values['with_what'] = with_what

                    # Extract amount paid with simple pattern matching (SpaCy doesn't handle numbers well by default)
                    for token in doc:
                        if not amount_paid and token.like_num:
                            if 'naira' in text or '₦' in text or 'ngn' in text:
                                try:
                                    amount_paid = float(token.text)
                                    field_values['amount_paid'] = str(amount_paid)
                                except ValueError:
                                    pass

                    # Check for missing fields
                    required_fields = ['product_category', 'brand', 'amount_paid', 'purchase_location', 'consume_location', 'with_whom', 'with_what']
                    missing_fields = [field for field in required_fields if not field_values[field]]

                    if missing_fields:
                        logging.debug(f"Missing fields in AI capture: {missing_fields}, field_values: {field_values}")
                        os.remove(file_path)
                        if os.path.exists(audio_path):
                            os.remove(audio_path)
                        return jsonify({
                            'error': 'Missing required fields in video audio',
                            'missing_fields': missing_fields,
                            'field_values': field_values
                        }), 400
                    else:
                        # Return extracted fields for review
                        os.remove(audio_path)
                        return jsonify({
                            'success': False,
                            'message': 'Review extracted data',
                            'fields': field_values
                        }), 200

                except Exception as e:
                    logging.error(f"AI audio analysis failed: {str(e)}")
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    if os.path.exists(audio_path):
                        os.remove(audio_path)
                    return jsonify({"error": f"AI analysis failed: {str(e)}"}), 500

        # Save to database (only for manual or confirmed AI submission)
        try:
            cur = mysql.connection.cursor()
            cur.execute("""
                INSERT INTO consumption_records 
                (user_id, product_category, brand, sku, amount_paid, purchase_location, consume_location, 
                 with_whom, with_what, additional_product_category, additional_brand, additional_sku, 
                 latitude, longitude, media_path)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id, product_category, brand, sku, amount_paid, purchase_location, consume_location,
                with_whom, with_what, additional_product_category, additional_brand, additional_sku,
                latitude, longitude, media_path
            ))
            mysql.connection.commit()
            cur.close()
            logging.debug("Consumption recorded successfully")
            flash('Consumption recorded successfully!', 'success')
            return jsonify({"success": True, "redirect": url_for('dashboard')})
        except Exception as e:
            logging.error(f"Database error: {str(e)}")
            if media_path and os.path.exists(media_path):
                os.remove(media_path)
            return jsonify({"error": f"Database error: {str(e)}"}), 500
    except Exception as e:
        logging.error(f"Error submitting consumption: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/instructions')
@login_required
def instructions():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    return render_template('instructions.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/admin_dashboard')
@login_required
@admin_required
def admin_dashboard():
    cur = mysql.connection.cursor()
    
    # Total Consumption Overview
    cur.execute("SELECT COUNT(*) FROM consumption_records")
    total_entries = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT brand) FROM consumption_records")
    unique_brands = cur.fetchone()[0]
    cur.execute("SELECT AVG(amount_paid) FROM consumption_records")
    avg_spending = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(DISTINCT purchase_location) FROM consumption_records")
    locations_visited = cur.fetchone()[0]
    cur.execute("SELECT purchase_location, COUNT(*) as count FROM consumption_records GROUP BY purchase_location ORDER BY count DESC LIMIT 1")
    top_purchase_location = cur.fetchone()[0] if cur.rowcount > 0 else None

    # Filters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    selected_category = request.args.get('category')
    selected_user_email = request.args.get('user_email')
    query_base = """
        SELECT cr.product_category, cr.brand, cr.amount_paid, cr.purchase_location, cr.created_at, u.email
        FROM consumption_records cr
        JOIN users u ON cr.user_id = u.id
    """
    params = []
    if start_date and end_date:
        query_base += " WHERE cr.created_at BETWEEN %s AND %s"
        params.extend([start_date, end_date])
    elif start_date:
        query_base += " WHERE cr.created_at >= %s"
        params.append(start_date)
    elif end_date:
        query_base += " WHERE cr.created_at <= %s"
        params.append(end_date)
    if selected_category and selected_category != "All Categories":
        query_base += " AND cr.product_category = %s" if "WHERE" not in query_base else " WHERE cr.product_category = %s"
        params.append(selected_category)
    if selected_user_email and selected_user_email != "All Users":
        query_base += " AND u.email = %s" if "WHERE" not in query_base else " WHERE u.email = %s"
        params.append(selected_user_email)

    # Spending Trends by Category
    cur.execute(query_base + " GROUP BY cr.product_category", params)
    spending_by_category = []
    total_spent = cur.execute("SELECT SUM(amount_paid) FROM consumption_records" + (" WHERE " + " AND ".join([f"{k} = %s" for k in params if k]) if params else ""), params).fetchone()[0] or 0
    for row in cur.fetchall():
        category = row[0]
        cur.execute(query_base + " AND cr.product_category = %s GROUP BY cr.product_category", params + [category])
        spent = cur.fetchone()[2] or 0
        spending_by_category.append({
            'category': category,
            'total_spent': spent,
            'percentage': (spent / total_spent * 100) if total_spent else 0
        })

    # User Activity Heatmap
    cur.execute(query_base + " GROUP BY DATE(cr.created_at)", params)
    activity_data = cur.fetchall()
    heatmap_data = []
    from datetime import datetime, timedelta
    end = datetime.now()
    start = end - timedelta(days=14)
    current = start
    while current <= end:
        week_data = {'date': current.strftime('%Y-%m-%d'), 'counts': []}
        for i in range(7):
            day = current + timedelta(days=i)
            count = sum(1 for row in activity_data if datetime.strptime(row[4].strftime('%Y-%m-%d'), '%Y-%m-%d').date() == day.date())
            week_data['counts'].append(count)
        heatmap_data.append(week_data)
        current += timedelta(days=7)
    heatmap_days = [start + timedelta(days=i) for i in range(7)]
    heatmap_days = [d.strftime('%a') for d in heatmap_days]

    # Location-Based Analysis
    cur.execute(query_base + " GROUP BY cr.purchase_location", params)
    location_analysis = []
    for row in cur.fetchall():
        location = row[3]
        cur.execute(query_base + " AND cr.purchase_location = %s", params + [location])
        records = cur.fetchall()
        transaction_count = len(records)
        total_spent = sum(r[2] for r in records) or 0
        location_analysis.append({
            'location': location,
            'transaction_count': transaction_count,
            'total_spent': total_spent,
            'percentage': (total_spent / total_spent * 100) if total_spent else 0
        })

    # Categories and Users for Dropdowns
    cur.execute("SELECT DISTINCT product_category FROM consumption_records")
    categories = [row[0] for row in cur.fetchall() if row[0]]
    cur.execute("SELECT DISTINCT email FROM users")
    user_emails = [row[0] for row in cur.fetchall() if row[0]]

    cur.close()
    return render_template('admin_dashboard.html', 
                          total_entries=total_entries, 
                          unique_brands=unique_brands, 
                          avg_spending=avg_spending, 
                          locations_visited=locations_visited, 
                          top_purchase_location=top_purchase_location,
                          spending_by_category=spending_by_category,
                          heatmap_data=heatmap_data,
                          heatmap_days=heatmap_days,
                          location_analysis=location_analysis,
                          categories=categories,
                          user_emails=user_emails,
                          start_date=start_date,
                          end_date=end_date,
                          selected_category=selected_category,
                          selected_user_email=selected_user_email)

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', name=current_user.name)

@app.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    name = request.form.get('name')
    if name:
        try:
            cur = mysql.connection.cursor()
            cur.execute("UPDATE users SET name = %s, updated_at = NOW() WHERE id = %s", (name, current_user.id))
            mysql.connection.commit()
            cur.execute("SELECT name FROM users WHERE id = %s", (current_user.id,))
            updated_name = cur.fetchone()[0]
            current_user.name = updated_name  # Update the User object in the session
            cur.close()
            flash('Profile updated successfully!', 'success')
        except Exception as e:
            logging.error(f"Profile update failed: {str(e)}")
            flash('Error updating profile.', 'danger')
            cur.close()
    return redirect(url_for('profile'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)