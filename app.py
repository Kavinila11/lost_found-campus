from flask import Flask, render_template, request, redirect, url_for, flash
from flask_pymongo import PyMongo
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from flask_bcrypt import Bcrypt
from bson.objectid import ObjectId
from bson.errors import InvalidId
from datetime import datetime
import os, uuid
from werkzeug.utils import secure_filename

# ── CONFIG ─────────────────────────────────────────────
class Config:
    SECRET_KEY = 'your_secret_key'
    MONGO_URI = 'mongodb://localhost:27017/lostfound'
    UPLOAD_FOLDER = 'static/uploads'
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# ── USER MODEL 
class User(UserMixin):
    def __init__(self, user_doc):
        self.id = str(user_doc['_id'])
        self.username = user_doc['username']
        self.email = user_doc['email']

# ── APP SETUP 
app = Flask(__name__)
app.config.from_object(Config)

mongo = PyMongo(app)
bcrypt = Bcrypt(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ── HELPERS
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@login_manager.user_loader
def load_user(user_id):
    try:
        user_doc = mongo.db.users.find_one({'_id': ObjectId(user_id)})
        return User(user_doc) if user_doc else None
    except InvalidId:
        return None

# ── HOME 
@app.route('/')
def index():
    category = request.args.get('category', '')
    item_type = request.args.get('type', '')

    query = {}
    if category:
        query['category'] = category
    if item_type in ['lost', 'found']:
        query['item_type'] = item_type

    items = list(mongo.db.items.find(query).sort('posted_on', -1))
    return render_template('index.html', items=items)

# ── REGISTER 
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not username or not email or not password:
            flash('All fields are required!', 'danger')
            return redirect(url_for('register'))

        existing = mongo.db.users.find_one({
            '$or': [{'email': email}, {'username': username}]
        })

        if existing:
            flash('Username or email already exists.', 'danger')
            return redirect(url_for('register'))

        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')

        mongo.db.users.insert_one({
            'username': username,
            'email': email,
            'password': hashed_pw,
            'joined': datetime.utcnow()
        })

        flash('Account created! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

# ── LOGIN 
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        user_doc = mongo.db.users.find_one({'email': email})

        if user_doc and bcrypt.check_password_hash(user_doc['password'], password):
            user = User(user_doc)
            login_user(user)
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('index'))

        flash('Invalid email or password.', 'danger')

    return render_template('login.html')

# ── LOGOUT 
@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('index'))

# ── POST ITEM 
@app.route('/post', methods=['GET', 'POST'])
@login_required
def post_item():
    if request.method == 'POST':
        title = request.form.get('title', '')
        description = request.form.get('description', '')
        category = request.form.get('category', '')
        location = request.form.get('location', '')
        item_type = request.form.get('item_type', '')
        date_lost = request.form.get('date_lost', '')

        image_filename = None
        file = request.files.get('image')

        if file and allowed_file(file.filename):
            ext = file.filename.rsplit('.', 1)[1].lower()
            image_filename = str(uuid.uuid4()) + '.' + ext
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))

        item = {
            'title': title,
            'description': description,
            'category': category,
            'location': location,
            'item_type': item_type,
            'date_lost': date_lost,
            'image': image_filename,
            'status': 'open',
            'posted_by': current_user.id,
            'posted_by_name': current_user.username,
            'posted_on': datetime.utcnow(),
            'claims': []
        }

        mongo.db.items.insert_one(item)
        flash('Item posted successfully!', 'success')
        return redirect(url_for('index'))

    return render_template('post_item.html')

# ── ITEM DETAILS
@app.route('/item/<item_id>')
def item_detail(item_id):
    try:
        item = mongo.db.items.find_one_or_404({'_id': ObjectId(item_id)})
        return render_template('item_detail.html', item=item)
    except InvalidId:
        flash('Invalid item ID', 'danger')
        return redirect(url_for('index'))

# ── CLAIM ITEM 
@app.route('/claim/<item_id>', methods=['POST'])
@login_required
def claim_item(item_id):
    message = request.form.get('message', '')

    existing_claim = mongo.db.items.find_one({
        '_id': ObjectId(item_id),
        'claims.claimant_id': current_user.id
    })

    if existing_claim:
        flash('You already claimed this item.', 'warning')
        return redirect(url_for('item_detail', item_id=item_id))

    claim = {
        'claimant_id': current_user.id,
        'claimant_name': current_user.username,
        'message': message,
        'claimed_on': datetime.utcnow(),
        'status': 'pending'
    }

    mongo.db.items.update_one(
        {'_id': ObjectId(item_id)},
        {'$push': {'claims': claim}}
    )

    flash('Claim submitted!', 'success')
    return redirect(url_for('item_detail', item_id=item_id))

# ── SEARCH 
@app.route('/search')
def search():
    query_text = request.args.get('q', '').strip()
    results = []

    if query_text:
        results = list(mongo.db.items.find({
            '$or': [
                {'title': {'$regex': query_text, '$options': 'i'}},
                {'description': {'$regex': query_text, '$options': 'i'}},
                {'location': {'$regex': query_text, '$options': 'i'}},
                {'category': {'$regex': query_text, '$options': 'i'}}
            ]
        }).sort('posted_on', -1))

    return render_template('search_results.html', results=results, query=query_text)

# ── DASHBOARD ──────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    my_items = list(
        mongo.db.items.find({'posted_by': current_user.id}).sort('posted_on', -1)
    )
    return render_template('dashboard.html', my_items=my_items)

# ── RESOLVE ITEM ───────────────────────────────────────
@app.route('/resolve/<item_id>/<action>')
@login_required
def resolve_claim(item_id, action):
    item = mongo.db.items.find_one({'_id': ObjectId(item_id)})

    if not item or item['posted_by'] != current_user.id:
        flash('Unauthorized action', 'danger')
        return redirect(url_for('dashboard'))

    if action == 'close':
        mongo.db.items.update_one(
            {'_id': ObjectId(item_id)},
            {'$set': {'status': 'closed'}}
        )
        flash('Item marked as resolved.', 'success')

    return redirect(url_for('dashboard'))

# ── RUN ────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True)
