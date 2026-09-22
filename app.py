from flask import Flask, request, render_template, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import os
import re
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-later')
db = SQLAlchemy(app)

UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
GROUP_PHOTO_FOLDER = os.path.join('static', 'group_photos')

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['GROUP_PHOTO_FOLDER'] = GROUP_PHOTO_FOLDER


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# the flask-login creation
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'  # this helps redirecting the user to login again


# creating the user model
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    profile_pic = db.Column(db.String(200), nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def avatar_url(self):
        if self.profile_pic:
            return url_for('static', filename=f'uploads/{self.profile_pic}')
        return f'https://ui-avatars.com/api/?name={self.username}&background=random&size=64'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class FriendRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')

    sender = db.relationship('User', foreign_keys=[sender_id], backref='sent_requests')
    receiver = db.relationship('User', foreign_keys=[receiver_id], backref='received_requests')


# Friendship model — created once a request is accepted
class Friendship(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    friend_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    user = db.relationship('User', foreign_keys=[user_id])
    friend = db.relationship('User', foreign_keys=[friend_id])


# --- Group model ---
class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    country = db.Column(db.String(100), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    creator = db.relationship('User', foreign_keys=[created_by], backref='groups_created')


# --- Membership model (links Users to Groups) ---
class Membership(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)

    user = db.relationship('User', foreign_keys=[user_id], backref='memberships')
    group = db.relationship('Group', foreign_keys=[group_id], backref='members')


# --- Message model (group chat) ---
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())
    is_bot = db.Column(db.Boolean, default=False)

    group = db.relationship('Group', foreign_keys=[group_id], backref='messages')
    sender = db.relationship('User', foreign_keys=[sender_id])


# --- Group Photo model (shared trip album) ---
class GroupPhoto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    uploader_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    caption = db.Column(db.String(300), nullable=True)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())

    group = db.relationship('Group', foreign_keys=[group_id], backref='photos')
    uploader = db.relationship('User', foreign_keys=[uploader_id])

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(GROUP_PHOTO_FOLDER, exist_ok=True)

# --- Trip model (confirmed dates for a group's trip) ---
class Trip(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False, unique=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)

    group = db.relationship('Group', foreign_keys=[group_id], backref=db.backref('trip', uselist=False))


# --- ItineraryDay model (one row per day of the trip) ---
class ItineraryDay(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    trip_id = db.Column(db.Integer, db.ForeignKey('trip.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    activity = db.Column(db.Text, nullable=True)

    trip = db.relationship('Trip', foreign_keys=[trip_id], backref='days')


@app.context_processor
def inject_pending_count():
    if current_user.is_authenticated:
        count = FriendRequest.query.filter_by(
            receiver_id=current_user.id, status='pending'
        ).count()
        return {'pending_request_count': count}
    return {'pending_request_count': 0}


# routes
@app.route('/')
def home():
    return render_template('home.html')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email')
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(email=email).first():
            flash('Email already registered!')
            return redirect(url_for('signup'))
        if User.query.filter_by(username=username).first():
            flash('Username already taken!')
            return redirect(url_for('signup'))

        new_user = User(email=email, username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        flash('Account created! Please log in.')
        return redirect(url_for('login'))

    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password!')
            return redirect(url_for('login'))

    return render_template('login.html')


@app.route('/users')
@login_required
def users():
    search_query = request.args.get('search', '').strip()

    if search_query:
        results = User.query.filter(
            User.username.ilike(f'%{search_query}%'),
            User.id != current_user.id
        ).all()
    else:
        results = []

    friendships = Friendship.query.filter_by(user_id=current_user.id).all()
    friend_ids = {f.friend_id for f in friendships}

    sent_requests = FriendRequest.query.filter_by(
        sender_id=current_user.id, status='pending'
    ).all()
    pending_ids = {r.receiver_id for r in sent_requests}

    return render_template(
        'users.html',
        results=results,
        search_query=search_query,
        friend_ids=friend_ids,
        pending_ids=pending_ids
    )


@app.route('/friend-request/<int:receiver_id>', methods=['POST'])
@login_required
def send_friend_request(receiver_id):
    if receiver_id == current_user.id:
        flash("You can't send a friend request to yourself!")
        return redirect(url_for('dashboard'))

    existing = FriendRequest.query.filter_by(
        sender_id=current_user.id, receiver_id=receiver_id
    ).first()

    if existing:
        flash('Friend request already sent!')
        return redirect(url_for('dashboard'))

    new_request = FriendRequest(sender_id=current_user.id, receiver_id=receiver_id)
    db.session.add(new_request)
    db.session.commit()

    flash('Friend request sent!')
    return redirect(url_for('dashboard'))


@app.route('/friend-request/<int:request_id>/accept', methods=['POST'])
@login_required
def accept_friend_request(request_id):
    req = FriendRequest.query.get_or_404(request_id)

    if req.receiver_id != current_user.id:
        flash("You can't accept this request.")
        return redirect(url_for('dashboard'))

    req.status = 'accepted'

    friendship1 = Friendship(user_id=req.sender_id, friend_id=req.receiver_id)
    friendship2 = Friendship(user_id=req.receiver_id, friend_id=req.sender_id)
    db.session.add(friendship1)
    db.session.add(friendship2)
    db.session.commit()

    flash(f'You are now friends with {req.sender.username}!')
    return redirect(url_for('dashboard'))


@app.route('/friend-request/<int:request_id>/decline', methods=['POST'])
@login_required
def decline_friend_request(request_id):
    req = FriendRequest.query.get_or_404(request_id)

    if req.receiver_id != current_user.id:
        flash("You can't decline this request.")
        return redirect(url_for('dashboard'))

    req.status = 'declined'
    db.session.commit()

    flash('Friend request declined.')
    return redirect(url_for('dashboard'))


@app.route('/create-group', methods=['GET', 'POST'])
@login_required
def create_group():
    if request.method == 'POST':
        name = request.form.get('name')
        country = request.form.get('country')

        new_group = Group(name=name, country=country, created_by=current_user.id)
        db.session.add(new_group)
        db.session.commit()

        membership = Membership(user_id=current_user.id, group_id=new_group.id)
        db.session.add(membership)
        db.session.commit()

        flash(f'Group "{name}" created!')
        return redirect(url_for('dashboard'))

    return render_template('create_group.html')


@app.route('/group/<int:group_id>')
@login_required
def view_group(group_id):
    group = Group.query.get_or_404(group_id)

    is_member = Membership.query.filter_by(user_id=current_user.id, group_id=group_id).first()
    if not is_member:
        flash("You're not a member of this group.")
        return redirect(url_for('dashboard'))

    members = [m.user for m in group.members]
    member_ids = {m.id for m in members}

    friendships = Friendship.query.filter_by(user_id=current_user.id).all()
    addable_friends = [f.friend for f in friendships if f.friend.id not in member_ids]

    messages = Message.query.filter_by(group_id=group_id).order_by(Message.timestamp.asc()).all()

    return render_template(
        'group.html',
        group=group,
        members=members,
        addable_friends=addable_friends,
        messages=messages
    )


@app.route('/group/<int:group_id>/add-member', methods=['POST'])
@login_required
def add_member(group_id):
    group = Group.query.get_or_404(group_id)

    is_member = Membership.query.filter_by(user_id=current_user.id, group_id=group_id).first()
    if not is_member:
        flash("You're not a member of this group.")
        return redirect(url_for('dashboard'))

    friend_id = request.form.get('friend_id')

    is_friend = Friendship.query.filter_by(user_id=current_user.id, friend_id=friend_id).first()
    if not is_friend:
        flash("You can only add friends to the group.")
        return redirect(url_for('view_group', group_id=group_id))

    already_member = Membership.query.filter_by(user_id=friend_id, group_id=group_id).first()
    if already_member:
        flash("They're already in this group.")
        return redirect(url_for('view_group', group_id=group_id))

    new_membership = Membership(user_id=friend_id, group_id=group_id)
    db.session.add(new_membership)
    db.session.commit()

    flash("Friend added to the group!")
    return redirect(url_for('view_group', group_id=group_id))


@app.route('/group/<int:group_id>/leave', methods=['POST'])
@login_required
def leave_group(group_id):
    membership = Membership.query.filter_by(user_id=current_user.id, group_id=group_id).first()

    if not membership:
        flash("You are not a member of this group!")
        return redirect(url_for('dashboard'))

    db.session.delete(membership)
    db.session.commit()

    flash("You have left the group!")
    return redirect(url_for('dashboard'))


@app.route('/group/<int:group_id>/send-message', methods=['POST'])
@login_required
def send_message(group_id):
    is_member = Membership.query.filter_by(user_id=current_user.id, group_id=group_id).first()
    if not is_member:
        return {'error': 'not a member'}, 403

    content = request.form.get('content', '').strip()

    if content:
        new_message = Message(group_id=group_id, sender_id=current_user.id, content=content)
        db.session.add(new_message)
        db.session.commit()

        # check if Mimi was called
        if 'mimi' in content.lower():
            group = Group.query.get(group_id)

            history = Message.query.filter_by(group_id=group_id).order_by(Message.timestamp.asc()).all()

            system_prompt = (
                f"You are Mimi, a friendly and knowledgeable travel planning assistant helping a group plan a trip "
                f"to {group.country}. You are part of their group chat, and you only speak when someone calls your "
                "name ('mimi'). Talk about what the destination is known for, its top attractions, where tourists "
                "usually go, and also recommend lesser-known hidden gems. Ask the group about their budget, "
                "preferred scenery, and activities so you can tailor suggestions to what they actually want. "
                "Keep your answers conversational, warm, and not overly long, like a real group chat message.\n\n"
                "IMPORTANT: If the group confirms specific trip dates (a start date and end date) and asks you to "
                "lock in or confirm the trip, end your reply with this exact line on its own, using YYYY-MM-DD format:\n"
                "[CONFIRM_TRIP: start_date to end_date]\n"
                "For example: [CONFIRM_TRIP: 2026-07-10 to 2026-07-15]\n"
                "Only include this line when dates are clearly confirmed, not when just discussing possible dates.\n\n"
                "When you confirm trip dates, also propose a simple day-by-day plan based on what the group discussed "
                "(interests, budget, pace). Right after your [CONFIRM_TRIP] line, include a day-by-day breakdown in "
                "this exact format, one line per date between the start and end date (inclusive), using YYYY-MM-DD:\n"
                "[ITINERARY]\n"
                "YYYY-MM-DD: short activity description\n"
                "YYYY-MM-DD: short activity description\n"
                "[/ITINERARY]\n"
                "Keep each day's description brief (one sentence). Only include this when you're also confirming dates."
            )

            gemini_history = []
            for msg in history[-15:]:
                if msg.is_bot:
                    gemini_history.append({"role": "model", "parts": [msg.content]})
                else:
                    speaker = msg.sender.username if msg.sender else "someone"
                    gemini_history.append({"role": "user", "parts": [f"{speaker}: {msg.content}"]})

            try:
                model = genai.GenerativeModel(
                    model_name="models/gemini-3.6-flash",
                    system_instruction=system_prompt
                )
                chat = model.start_chat(history=gemini_history[:-1])
                response = chat.send_message(gemini_history[-1]["parts"][0])
                bot_reply = response.text

                # check if Mimi confirmed trip dates
                match = re.search(r'\[CONFIRM_TRIP:\s*(\d{4}-\d{2}-\d{2})\s*to\s*(\d{4}-\d{2}-\d{2})\]', bot_reply)
                if match:
                    start_date = datetime.strptime(match.group(1), '%Y-%m-%d').date()
                    end_date = datetime.strptime(match.group(2), '%Y-%m-%d').date()

                    existing_trip = Trip.query.filter_by(group_id=group_id).first()
                    if existing_trip:
                        ItineraryDay.query.filter_by(trip_id=existing_trip.id).delete()
                        existing_trip.start_date = start_date
                        existing_trip.end_date = end_date
                        trip = existing_trip
                    else:
                        trip = Trip(group_id=group_id, start_date=start_date, end_date=end_date)
                        db.session.add(trip)
                    db.session.commit()

                    current_date = start_date
                    while current_date <= end_date:
                        db.session.add(ItineraryDay(trip_id=trip.id, date=current_date))
                        current_date += timedelta(days=1)
                    db.session.commit()

                    # check if Mimi also included a day-by-day itinerary
                    itinerary_match = re.search(r'\[ITINERARY\](.*?)\[/ITINERARY\]', bot_reply, re.DOTALL)
                    if itinerary_match:
                        itinerary_text = itinerary_match.group(1).strip()
                        for line in itinerary_text.split('\n'):
                            line = line.strip()
                            day_match = re.match(r'(\d{4}-\d{2}-\d{2}):\s*(.+)', line)
                            if day_match:
                                day_date = datetime.strptime(day_match.group(1), '%Y-%m-%d').date()
                                activity_text = day_match.group(2).strip()

                                day_row = ItineraryDay.query.filter_by(trip_id=trip.id, date=day_date).first()
                                if day_row:
                                    day_row.activity = activity_text

                        db.session.commit()
                        bot_reply = bot_reply.replace(itinerary_match.group(0), '').strip()

                    bot_reply = bot_reply.replace(match.group(0), '').strip()
                    bot_reply += "\n\n✅ Trip dates confirmed and added to your calendar!"

            except Exception as e:
                print(f"GEMINI ERROR: {e}")
                bot_reply = "Sorry, I'm having trouble responding right now!"

            bot_message = Message(group_id=group_id, sender_id=None, content=bot_reply, is_bot=True)
            db.session.add(bot_message)
            db.session.commit()

    return {'status': 'ok'}


@app.route('/group/<int:group_id>/upload-photo', methods=['POST'])
@login_required
def upload_group_photo(group_id):
    is_member = Membership.query.filter_by(user_id=current_user.id, group_id=group_id).first()
    if not is_member:
        flash("You're not a member of this group.")
        return redirect(url_for('dashboard'))

    file = request.files.get('photo')
    caption = request.form.get('caption', '').strip()

    if not file or file.filename == '':
        flash('No file selected.')
        return redirect(url_for('view_group', group_id=group_id))

    if not allowed_file(file.filename):
        flash('Invalid file type. Please upload a PNG, JPG, JPEG, or GIF.')
        return redirect(url_for('view_group', group_id=group_id))

    filename = secure_filename(f'{group_id}_{current_user.id}_{file.filename}')
    filepath = os.path.join(app.config['GROUP_PHOTO_FOLDER'], filename)
    file.save(filepath)

    new_photo = GroupPhoto(
        group_id=group_id,
        uploader_id=current_user.id,
        filename=filename,
        caption=caption
    )
    db.session.add(new_photo)
    db.session.commit()

    flash('Photo added to the album!')
    return redirect(url_for('view_group', group_id=group_id))


@app.route('/group/<int:group_id>/album')
@login_required
def view_album(group_id):
    group = Group.query.get_or_404(group_id)

    is_member = Membership.query.filter_by(user_id=current_user.id, group_id=group_id).first()
    if not is_member:
        flash("You're not a member of this group.")
        return redirect(url_for('dashboard'))

    photos = GroupPhoto.query.filter_by(group_id=group_id).order_by(GroupPhoto.timestamp.asc()).all()

    return render_template('album.html', group=group, photos=photos)


@app.route('/group/<int:group_id>/messages')
@login_required
def get_message(group_id):
    is_member = Membership.query.filter_by(user_id=current_user.id, group_id=group_id).first()
    if not is_member:
        return {'error': 'not a member'}, 403

    messages = Message.query.filter_by(group_id=group_id).order_by(Message.timestamp.asc()).all()

    messages_data = [
        {
            'username': 'Mimi 🤖' if m.is_bot else m.sender.username,
            'content': m.content,
            'timestamp': m.timestamp.strftime('%b %d, %I:%M %p'),
            'is_bot': m.is_bot
        }
        for m in messages
    ]
    return {'messages': messages_data}


@app.route('/group/<int:group_id>/set-trip-dates', methods=['GET', 'POST'])
@login_required
def set_trip_dates(group_id):
    group = Group.query.get_or_404(group_id)
    is_member = Membership.query.filter_by(user_id=current_user.id, group_id=group_id).first()
    if not is_member:
        flash("You're not a member of this group.")
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        start_date_str = request.form.get('start_date')
        end_date_str = request.form.get('end_date')

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

        if end_date < start_date:
            flash('End date must be after start date.')
            return redirect(url_for('set_trip_dates', group_id=group_id))

        existing_trip = Trip.query.filter_by(group_id=group_id).first()
        if existing_trip:
            ItineraryDay.query.filter_by(trip_id=existing_trip.id).delete()
            existing_trip.start_date = start_date
            existing_trip.end_date = end_date
            trip = existing_trip
        else:
            trip = Trip(group_id=group_id, start_date=start_date, end_date=end_date)
            db.session.add(trip)

        db.session.commit()

        current_date = start_date
        while current_date <= end_date:
            day = ItineraryDay(trip_id=trip.id, date=current_date)
            db.session.add(day)
            current_date += timedelta(days=1)

        db.session.commit()

        flash('Trip dates confirmed! You can now plan each day.')
        return redirect(url_for('view_itinerary', group_id=group_id))

    return render_template('set_trip_dates.html', group=group)


@app.route('/group/<int:group_id>/itinerary')
@login_required
def view_itinerary(group_id):
    group = Group.query.get_or_404(group_id)
    is_member = Membership.query.filter_by(user_id=current_user.id, group_id=group_id).first()
    if not is_member:
        flash("You're not a member of this group.")
        return redirect(url_for('dashboard'))

    trip = Trip.query.filter_by(group_id=group_id).first()
    days = []
    if trip:
        days = ItineraryDay.query.filter_by(trip_id=trip.id).order_by(ItineraryDay.date.asc()).all()

    return render_template('itinerary.html', group=group, trip=trip, days=days)


@app.route('/itinerary-day/<int:day_id>/update', methods=['POST'])
@login_required
def update_itinerary_day(day_id):
    day = ItineraryDay.query.get_or_404(day_id)
    trip = day.trip
    group_id = trip.group_id

    is_member = Membership.query.filter_by(user_id=current_user.id, group_id=group_id).first()
    if not is_member:
        flash("You're not a member of this group.")
        return redirect(url_for('dashboard'))

    day.activity = request.form.get('activity', '').strip()
    db.session.commit()

    return redirect(url_for('view_itinerary', group_id=group_id))


@app.route('/calendar')
@login_required
def calendar():
    today = datetime.now().date()

    my_memberships = Membership.query.filter_by(user_id=current_user.id).all()
    my_group_ids = [m.group_id for m in my_memberships]

    trips = Trip.query.filter(Trip.group_id.in_(my_group_ids)).order_by(Trip.start_date.asc()).all()

    upcoming_trips = []
    past_trips = []

    for trip in trips:
        days = ItineraryDay.query.filter_by(trip_id=trip.id).order_by(ItineraryDay.date.asc()).all()
        trip_data = {
            'group': trip.group,
            'trip': trip,
            'days': days
        }
        if trip.end_date >= today:
            upcoming_trips.append(trip_data)
        else:
            past_trips.append(trip_data)

    return render_template(
        'calendar.html',
        upcoming_trips=upcoming_trips,
        past_trips=past_trips,
        today=today
    )


@app.route('/dashboard')
@login_required
def dashboard():
    pending_requests = FriendRequest.query.filter_by(
        receiver_id=current_user.id, status='pending'
    ).all()

    friendships = Friendship.query.filter_by(user_id=current_user.id).all()
    friends = [f.friend for f in friendships]

    my_memberships = Membership.query.filter_by(user_id=current_user.id).all()
    my_groups = [m.group for m in my_memberships]

    return render_template(
        'dashboard.html',
        pending_requests=pending_requests,
        friends=friends,
        my_groups=my_groups
    )


@app.route('/upload-avatar', methods=['GET', 'POST'])
@login_required
def upload_avatar():
    if request.method == 'POST':
        file = request.files.get('avatar')

        if not file or file.filename == '':
            flash('No file selected!')
            return redirect(url_for('upload_avatar'))

        if not allowed_file(file.filename):
            flash('Invalid file type. Please upload a PNG, JPG, JPEG, or GIF.')
            return redirect(url_for('upload_avatar'))

        filename = secure_filename(f'{current_user.id}_{file.filename}')
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        current_user.profile_pic = filename
        db.session.commit()

        flash('Profile picture updated!')
        return redirect(url_for('dashboard'))

    return render_template('upload_avatar.html')

@app.route('/my-photos')
@login_required
def my_photos():
    my_memberships = Membership.query.filter_by(user_id=current_user.id).all()
    my_group_ids = [m.group_id for m in my_memberships]

    photos = GroupPhoto.query.filter(
        GroupPhoto.group_id.in_(my_group_ids)
    ).order_by(GroupPhoto.timestamp.desc()).all()

    return render_template('my_photos.html', photos=photos)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))


with app.app_context():
    db.create_all()  # this helps create the database tables if they do not exist

if __name__ == '__main__':
    app.run(debug=True)