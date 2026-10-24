from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
import collections
import json
import sqlite3
import hashlib
import math
import re
from datetime import datetime

app = Flask(__name__)
app.secret_key = '123456789' 
DATABASE = 'database.sqlite'

# Load censorship data
# WARNING! The censorship.dat file contains disturbing language when decrypted. 
# If you want to test whether moderation works, 
# you can trigger censorship using these words: 
# tier1badword, tier2badword, tier3badword
ENCRYPTED_FILE_PATH = 'censorship.dat'
fernet = Fernet('xpplx11wZUibz0E8tV8Z9mf-wwggzSrc21uQ17Qq2gg=')
with open(ENCRYPTED_FILE_PATH, 'rb') as encrypted_file:
    encrypted_data = encrypted_file.read()
decrypted_data = fernet.decrypt(encrypted_data)
MODERATION_CONFIG = json.loads(decrypted_data)
TIER1_WORDS = MODERATION_CONFIG['categories']['tier1_severe_violations']['words']
TIER2_PHRASES = MODERATION_CONFIG['categories']['tier2_spam_scams']['phrases']
TIER3_WORDS = MODERATION_CONFIG['categories']['tier3_mild_profanity']['words']

def get_db():
    """
    Connect to the application's configured database. The connection
    is unique for each request and will be reused if this is called
    again.
    """
    if 'db' not in g:
        g.db = sqlite3.connect(
            DATABASE,
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row

    return g.db


@app.teardown_appcontext
def close_connection(exception):
    """Closes the database again at the end of the request."""
    db = g.pop('db', None)

    if db is not None:
        db.close()


def query_db(query, args=(), one=False, commit=False):
    """
    Queries the database and returns a list of dictionaries, a single
    dictionary, or None. Also handles write operations.
    """
    db = get_db()
    
    # Using 'with' on a connection object implicitly handles transactions.
    # The 'with' statement will automatically commit if successful, 
    # or rollback if an exception occurs. This is safer.
    try:
        with db:
            cur = db.execute(query, args)
        
        # For SELECT statements, fetch the results after the transaction block
        if not commit:
            rv = cur.fetchall()
            return (rv[0] if rv else None) if one else rv
        
        # For write operations, we might want the cursor to get info like lastrowid
        return cur

    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None

@app.template_filter('datetimeformat')
def datetimeformat(value):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    else:
        return "N/A"
    return dt.strftime('%b %d, %Y %H:%M')

REACTION_EMOJIS = {
    'like': '❤️', 'love': '😍', 'laugh': '😂',
    'wow': '😮', 'sad': '😢', 'angry': '😠',
}
REACTION_TYPES = list(REACTION_EMOJIS.keys())


@app.route('/')
def feed():
    #  1. Get Pagination and Filter Parameters 
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1
    sort = request.args.get('sort', 'new').lower()
    show = request.args.get('show', 'all').lower()
    
    # Define how many posts to show per page
    POSTS_PER_PAGE = 10
    offset = (page - 1) * POSTS_PER_PAGE

    current_user_id = session.get('user_id')
    params = []

    #  2. Build the Query 
    where_clause = ""
    if show == 'following' and current_user_id:
        where_clause = "WHERE p.user_id IN (SELECT followed_id FROM follows WHERE follower_id = ?)"
        params.append(current_user_id)

    # Add the pagination parameters to the query arguments
    pagination_params = (POSTS_PER_PAGE, offset)

    if sort == 'popular':
        query = f"""
            SELECT p.id, p.content, p.created_at, u.username, u.id as user_id,
                   IFNULL(r.total_reactions, 0) as total_reactions
            FROM posts p
            JOIN users u ON p.user_id = u.id
            LEFT JOIN (
                SELECT post_id, COUNT(*) as total_reactions FROM reactions GROUP BY post_id
            ) r ON p.id = r.post_id
            {where_clause}
            ORDER BY total_reactions DESC, p.created_at DESC
            LIMIT ? OFFSET ?
        """
        final_params = params + list(pagination_params)
        posts = query_db(query, final_params)
    elif sort == 'recommended':
        posts = recommend(current_user_id, show == 'following' and current_user_id)
    else:  # Default sort is 'new'
        query = f"""
            SELECT p.id, p.content, p.created_at, u.username, u.id as user_id
            FROM posts p
            JOIN users u ON p.user_id = u.id
            {where_clause}
            ORDER BY p.created_at DESC
            LIMIT ? OFFSET ?
        """
        final_params = params + list(pagination_params)
        posts = query_db(query, final_params)

    posts_data = []
    for post in posts:
        # Determine if the current user follows the poster
        followed_poster = False
        if current_user_id and post['user_id'] != current_user_id:
            follow_check = query_db(
                'SELECT 1 FROM follows WHERE follower_id = ? AND followed_id = ?',
                (current_user_id, post['user_id']),
                one=True
            )
            if follow_check:
                followed_poster = True

        # Determine if the current user reacted to this post and with what reaction
        user_reaction = None
        if current_user_id:
            reaction_check = query_db(
                'SELECT reaction_type FROM reactions WHERE user_id = ? AND post_id = ?',
                (current_user_id, post['id']),
                one=True
            )
            if reaction_check:
                user_reaction = reaction_check['reaction_type']

        reactions = query_db('SELECT reaction_type, COUNT(*) as count FROM reactions WHERE post_id = ? GROUP BY reaction_type', (post['id'],))
        comments_raw = query_db('SELECT c.id, c.content, c.created_at, u.username, u.id as user_id FROM comments c JOIN users u ON c.user_id = u.id WHERE c.post_id = ? ORDER BY c.created_at ASC', (post['id'],))
        post_dict = dict(post)
        post_dict['content'], _ = moderate_content(post_dict['content'])
        comments_moderated = []
        for comment in comments_raw:
            comment_dict = dict(comment)
            comment_dict['content'], _ = moderate_content(comment_dict['content'])
            comments_moderated.append(comment_dict)
        posts_data.append({
            'post': post_dict,
            'reactions': reactions,
            'user_reaction': user_reaction,
            'followed_poster': followed_poster,
            'comments': comments_moderated
        })

    #  "People you may know" card at the top of the first page (Design Claim 14 feature)
    people_suggestions = None
    if current_user_id and page == 1:
        people_suggestions = get_people_suggestions(current_user_id, SUGGESTIONS_ON_FEED)

    #  4. Render Template with Pagination Info
    return render_template('feed.html.j2',
                           people_suggestions=people_suggestions,
                           posts=posts_data, 
                           current_sort=sort,
                           current_show=show,
                           page=page, # Pass current page number
                           per_page=POSTS_PER_PAGE, # Pass items per page
                           reaction_emojis=REACTION_EMOJIS,
                           reaction_types=REACTION_TYPES)

@app.route('/posts/new', methods=['POST'])
def add_post():
    """Handles creating a new post from the feed."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to create a post.', 'danger')
        return redirect(url_for('login'))

    # Get content from the submitted form
    content = request.form.get('content')

    # Pass the user's content through the moderation function
    moderated_content = content

    # Basic validation to ensure post is not empty
    if moderated_content and moderated_content.strip():
        db = get_db()
        db.execute('INSERT INTO posts (user_id, content) VALUES (?, ?)',
                   (user_id, moderated_content))
        db.commit()
        flash('Your post was successfully created!', 'success')
    else:
        # This will catch empty posts or posts that were fully censored
        flash('Post cannot be empty or was fully censored.', 'warning')

    # Redirect back to the main feed to see the new post
    return redirect(url_for('feed'))
    
    
@app.route('/posts/<int:post_id>/delete', methods=['POST'])
def delete_post(post_id):
    """Handles deleting a post."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to delete a post.', 'danger')
        return redirect(url_for('login'))

    # Find the post in the database
    post = query_db('SELECT id, user_id FROM posts WHERE id = ?', (post_id,), one=True)

    # Check if the post exists and if the current user is the owner
    if not post:
        flash('Post not found.', 'danger')
        return redirect(url_for('feed'))

    if post['user_id'] != user_id:
        # Security check: prevent users from deleting others' posts
        flash('You do not have permission to delete this post.', 'danger')
        return redirect(url_for('feed'))

    # If all checks pass, proceed with deletion
    db = get_db()
    # To maintain database integrity, delete associated records first
    db.execute('DELETE FROM comments WHERE post_id = ?', (post_id,))
    db.execute('DELETE FROM reactions WHERE post_id = ?', (post_id,))
    db.execute('DELETE FROM ask_requests WHERE post_id = ?', (post_id,))
    db.execute('UPDATE users SET pinned_post_id = NULL WHERE pinned_post_id = ?', (post_id,))
    # Finally, delete the post itself
    db.execute('DELETE FROM posts WHERE id = ?', (post_id,))
    db.commit()

    flash('Your post was successfully deleted.', 'success')
    # Redirect back to the page the user came from, or the feed as a fallback
    return redirect(request.referrer or url_for('feed'))

@app.route('/u/<username>')
def user_profile(username):
    """Displays a user's profile page with moderated bio, posts, and latest comments."""
    
    user_raw = query_db('SELECT * FROM users WHERE username = ?', (username,), one=True)
    if not user_raw:
        abort(404)

    user = dict(user_raw)
    moderated_bio, _ = moderate_content(user.get('profile', ''))
    user['profile'] = moderated_bio

    posts_raw = query_db('SELECT id, content, user_id, created_at FROM posts WHERE user_id = ? ORDER BY created_at DESC', (user['id'],))
    posts = []
    for post_raw in posts_raw:
        post = dict(post_raw)
        moderated_post_content, _ = moderate_content(post['content'])
        post['content'] = moderated_post_content
        posts.append(post)

    comments_raw = query_db('SELECT id, content, user_id, post_id, created_at FROM comments WHERE user_id = ? ORDER BY created_at DESC LIMIT 100', (user['id'],))
    comments = []
    for comment_raw in comments_raw:
        comment = dict(comment_raw)
        moderated_comment_content, _ = moderate_content(comment['content'])
        comment['content'] = moderated_comment_content
        comments.append(comment)

    followers_count = query_db('SELECT COUNT(*) as cnt FROM follows WHERE followed_id = ?', (user['id'],), one=True)['cnt']
    following_count = query_db('SELECT COUNT(*) as cnt FROM follows WHERE follower_id = ?', (user['id'],), one=True)['cnt']

    #  NEW: CHECK FOLLOW STATUS 
    is_currently_following = False # Default to False
    current_user_id = session.get('user_id')
    
    # We only need to check if a user is logged in
    if current_user_id:
        follow_relation = query_db(
            'SELECT 1 FROM follows WHERE follower_id = ? AND followed_id = ?',
            (current_user_id, user['id']),
            one=True
        )
        if follow_relation:
            is_currently_following = True
    # --

    #  Reputation level and pinned post (Design Claim 23 feature)
    reputation = get_level(user['id'])
    pinned_post = None
    if user.get('pinned_post_id'):
        pinned_post = next((post for post in posts if post['id'] == user['pinned_post_id']), None)
        # The pinned post is shown on top, so it doesn't need to appear again in the list
        posts = [post for post in posts if post['id'] != user['pinned_post_id']]

    return render_template('user_profile.html.j2',
                           user=user,
                           posts=posts,
                           comments=comments,
                           followers_count=followers_count,
                           following_count=following_count,
                           is_following=is_currently_following,
                           reputation=reputation,
                           pinned_post=pinned_post,
                           levels=LEVELS,
                           pin_level=PIN_LEVEL,
                           avatar_color_level=AVATAR_COLOR_LEVEL,
                           avatar_colors=AVATAR_COLORS,
                           points_per_reaction=POINTS_PER_REACTION,
                           points_per_comment=POINTS_PER_COMMENT,
                           max_points_per_member=MAX_POINTS_PER_MEMBER)
    

@app.route('/u/<username>/followers')
def user_followers(username):
    user = query_db('SELECT * FROM users WHERE username = ?', (username,), one=True)
    if not user:
        abort(404)
    followers = query_db('''
        SELECT u.username
        FROM follows f
        JOIN users u ON f.follower_id = u.id
        WHERE f.followed_id = ?
    ''', (user['id'],))
    return render_template('user_list.html.j2', user=user, users=followers, title="Followers of")

@app.route('/u/<username>/following')
def user_following(username):
    user = query_db('SELECT * FROM users WHERE username = ?', (username,), one=True)
    if not user:
        abort(404)
    following = query_db('''
        SELECT u.username
        FROM follows f
        JOIN users u ON f.followed_id = u.id
        WHERE f.follower_id = ?
    ''', (user['id'],))
    return render_template('user_list.html.j2', user=user, users=following, title="Users followed by")

@app.route('/posts/<int:post_id>')
def post_detail(post_id):
    """Displays a single post and its comments, with content moderation applied."""
    
    post_raw = query_db('''
        SELECT p.id, p.content, p.created_at, u.username, u.id as user_id
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.id = ?
    ''', (post_id,), one=True)

    if not post_raw:
        # The abort function will stop the request and show a 404 Not Found page.
        abort(404)

    #  Moderation for the Main Post 
    # Convert the raw database row to a mutable dictionary
    post = dict(post_raw)
    # Unpack the tuple from moderate_content, we only need the moderated content string here
    moderated_post_content, _ = moderate_content(post['content'])
    post['content'] = moderated_post_content

    #  Fetch Reactions (No moderation needed) 
    reactions = query_db('''
        SELECT reaction_type, COUNT(*) as count
        FROM reactions
        WHERE post_id = ?
        GROUP BY reaction_type
    ''', (post_id,))

    #  Fetch and Moderate Comments 
    comments_raw = query_db('SELECT c.id, c.content, c.created_at, u.username, u.id as user_id FROM comments c JOIN users u ON c.user_id = u.id WHERE c.post_id = ? ORDER BY c.created_at ASC', (post_id,))
    
    comments = [] # Create a new list for the moderated comments
    for comment_raw in comments_raw:
        comment = dict(comment_raw) # Convert to a dictionary
        # Moderate the content of each comment
        print(comment['content'])
        moderated_comment_content, _ = moderate_content(comment['content'])
        comment['content'] = moderated_comment_content
        comments.append(comment)

    # Pass the moderated data to the template
    return render_template('post_detail.html.j2',
                           post=post,
                           reactions=reactions,
                           comments=comments,
                           reaction_emojis=REACTION_EMOJIS,
                           reaction_types=REACTION_TYPES,
                           **get_ask_context(post, session.get('user_id')))

@app.route('/about')
def about():
    return render_template('about.html.j2')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html.j2')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        location = request.form.get('location', '')
        birthdate = request.form.get('birthdate', '')
        profile = request.form.get('profile', '')

        hashed_password = generate_password_hash(password)

        db = get_db()
        cur = db.cursor()
        try:
            cur.execute(
                'INSERT INTO users (username, password, location, birthdate, profile) VALUES (?, ?, ?, ?, ?)',
                (username, hashed_password, location, birthdate, profile)
            )
            db.commit()

            # 1. Get the ID of the user we just created.
            new_user_id = cur.lastrowid

            # 2. Add user info to the session cookie.
            session.clear() # Clear any old session data
            session['user_id'] = new_user_id
            session['username'] = username

            # 3. Flash a welcome message and redirect to the feed.
            flash(f'Welcome, {username}! Your account has been created.', 'success')
            return redirect(url_for('feed')) # Redirect to the main feed/dashboard

        except sqlite3.IntegrityError:
            flash('Username already taken. Please choose another one.', 'danger')
        finally:
            cur.close()
            db.close()
            
    return render_template('signup.html.j2')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        db.close()

        # 1. Check if the user exists.
        # 2. If user exists, use check_password_hash to securely compare the password.
        #    This function handles the salt and prevents timing attacks.
        if user and check_password_hash(user['password'], password):
            # Password is correct!
            session['user_id'] = user['id']
            session['username'] = user['username']
            flash('Logged in successfully.', 'success')
            return redirect(url_for('feed'))
        else:
            # User does not exist or password was incorrect.
            flash('Invalid username or password.', 'danger')
            
    return render_template('login.html.j2')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/posts/<int:post_id>/comment', methods=['POST'])
def add_comment(post_id):
    """Handles adding a new comment to a specific post."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to comment.', 'danger')
        return redirect(url_for('login'))

    # Get content from the submitted form
    content = request.form.get('content')

    # Basic validation to ensure comment is not empty
    if content and content.strip():
        db = get_db()
        db.execute('INSERT INTO comments (post_id, user_id, content) VALUES (?, ?, ?)',
                   (post_id, user_id, content))
        # If the author personally asked this member to answer, the request is now answered
        answered = db.execute('''
            UPDATE ask_requests SET status = 'answered', responded_at = CURRENT_TIMESTAMP
            WHERE post_id = ? AND recipient_id = ? AND status != 'answered'
        ''', (post_id, user_id)).rowcount
        db.commit()
        if answered:
            flash('Thanks for answering! The member who asked you will see your reply.', 'success')
        else:
            flash('Your comment was added.', 'success')
    else:
        flash('Comment cannot be empty.', 'warning')

    # Redirect back to the page the user came from (likely the post detail page)
    return redirect(request.referrer or url_for('post_detail', post_id=post_id))

@app.route('/comments/<int:comment_id>/delete', methods=['POST'])
def delete_comment(comment_id):
    """Handles deleting a comment."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to delete a comment.', 'danger')
        return redirect(url_for('login'))

    # Find the comment and the original post's author ID
    comment = query_db('''
        SELECT c.id, c.user_id, p.user_id as post_author_id
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        WHERE c.id = ?
    ''', (comment_id,), one=True)

    # Check if the comment exists
    if not comment:
        flash('Comment not found.', 'danger')
        return redirect(request.referrer or url_for('feed'))

    # Security Check: Allow deletion if the user is the comment's author OR the post's author
    if user_id != comment['user_id'] and user_id != comment['post_author_id']:
        flash('You do not have permission to delete this comment.', 'danger')
        return redirect(request.referrer or url_for('feed'))

    # If all checks pass, proceed with deletion
    db = get_db()
    db.execute('DELETE FROM comments WHERE id = ?', (comment_id,))
    db.commit()

    flash('Comment successfully deleted.', 'success')
    # Redirect back to the page the user came from
    return redirect(request.referrer or url_for('feed'))

@app.route('/react', methods=['POST'])
def add_reaction():
    """Handles adding a new reaction or updating an existing one."""
    user_id = session.get('user_id')

    if not user_id:
        flash("You must be logged in to react.", "danger")
        return redirect(url_for('login'))

    post_id = request.form.get('post_id')
    new_reaction_type = request.form.get('reaction')

    if not post_id or not new_reaction_type:
        flash("Invalid reaction request.", "warning")
        return redirect(request.referrer or url_for('feed'))

    db = get_db()

    # Step 1: Check if a reaction from this user already exists on this post.
    existing_reaction = query_db('SELECT id FROM reactions WHERE post_id = ? AND user_id = ?',
                                 (post_id, user_id), one=True)

    if existing_reaction:
        # Step 2: If it exists, UPDATE the reaction_type.
        db.execute('UPDATE reactions SET reaction_type = ? WHERE id = ?',
                   (new_reaction_type, existing_reaction['id']))
    else:
        # Step 3: If it does not exist, INSERT a new reaction.
        db.execute('INSERT INTO reactions (post_id, user_id, reaction_type) VALUES (?, ?, ?)',
                   (post_id, user_id, new_reaction_type))

    db.commit()

    return redirect(request.referrer or url_for('feed'))

@app.route('/unreact', methods=['POST'])
def unreact():
    """Handles removing a user's reaction from a post."""
    user_id = session.get('user_id')

    if not user_id:
        flash("You must be logged in to unreact.", "danger")
        return redirect(url_for('login'))

    post_id = request.form.get('post_id')

    if not post_id:
        flash("Invalid unreact request.", "warning")
        return redirect(request.referrer or url_for('feed'))

    db = get_db()

    # Remove the reaction if it exists
    existing_reaction = query_db(
        'SELECT id FROM reactions WHERE post_id = ? AND user_id = ?',
        (post_id, user_id),
        one=True
    )

    if existing_reaction:
        db.execute('DELETE FROM reactions WHERE id = ?', (existing_reaction['id'],))
        db.commit()
        flash("Reaction removed.", "success")
    else:
        flash("No reaction to remove.", "info")

    return redirect(request.referrer or url_for('feed'))


@app.route('/u/<int:user_id>/follow', methods=['POST'])
def follow_user(user_id):
    """Handles the logic for the current user to follow another user."""
    follower_id = session.get('user_id')

    # Security: Ensure user is logged in
    if not follower_id:
        flash("You must be logged in to follow users.", "danger")
        return redirect(url_for('login'))

    # Security: Prevent users from following themselves
    if follower_id == user_id:
        flash("You cannot follow yourself.", "warning")
        return redirect(request.referrer or url_for('feed'))

    # Check if the user to be followed actually exists
    user_to_follow = query_db('SELECT id FROM users WHERE id = ?', (user_id,), one=True)
    if not user_to_follow:
        flash("The user you are trying to follow does not exist.", "danger")
        return redirect(request.referrer or url_for('feed'))
        
    db = get_db()
    try:
        # Insert the follow relationship. The PRIMARY KEY constraint will prevent duplicates if you've set one.
        db.execute('INSERT INTO follows (follower_id, followed_id) VALUES (?, ?)',
                   (follower_id, user_id))
        db.commit()
        username_to_follow = query_db('SELECT username FROM users WHERE id = ?', (user_id,), one=True)['username']
        flash(f"You are now following {username_to_follow}.", "success")
    except sqlite3.IntegrityError:
        flash("You are already following this user.", "info")

    return redirect(request.referrer or url_for('feed'))


@app.route('/u/<int:user_id>/unfollow', methods=['POST'])
def unfollow_user(user_id):
    """Handles the logic for the current user to unfollow another user."""
    follower_id = session.get('user_id')

    # Security: Ensure user is logged in
    if not follower_id:
        flash("You must be logged in to unfollow users.", "danger")
        return redirect(url_for('login'))

    db = get_db()
    cur = db.execute('DELETE FROM follows WHERE follower_id = ? AND followed_id = ?',
               (follower_id, user_id))
    db.commit()

    if cur.rowcount > 0:
        # cur.rowcount tells us if a row was actually deleted
        username_unfollowed = query_db('SELECT username FROM users WHERE id = ?', (user_id,), one=True)['username']
        flash(f"You have unfollowed {username_unfollowed}.", "success")
    else:
        # This case handles if someone tries to unfollow a user they weren't following
        flash("You were not following this user.", "info")

    # Redirect back to the page the user came from
    return redirect(request.referrer or url_for('feed'))

@app.route('/admin')
def admin_dashboard():
    """Displays the admin dashboard with users, posts, and comments, sorted by risk."""

    if session.get('username') != 'admin':
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for('feed'))

    RISK_LEVELS = { "HIGH": 5, "MEDIUM": 3, "LOW": 1 }
    PAGE_SIZE = 50

    def get_risk_profile(score):
        if score >= RISK_LEVELS["HIGH"]:
            return "HIGH", 3
        elif score >= RISK_LEVELS["MEDIUM"]:
            return "MEDIUM", 2
        elif score >= RISK_LEVELS["LOW"]:
            return "LOW", 1
        return "NONE", 0

    # Get pagination and current tab parameters
    try:
        users_page = int(request.args.get('users_page', 1))
        posts_page = int(request.args.get('posts_page', 1))
        comments_page = int(request.args.get('comments_page', 1))
    except ValueError:
        users_page = 1
        posts_page = 1
        comments_page = 1
    
    current_tab = request.args.get('tab', 'users') # Default to 'users' tab

    users_offset = (users_page - 1) * PAGE_SIZE
    
    # First, get all users to calculate risk, then apply pagination in Python
    # It's more complex to do this efficiently in SQL if risk calc is Python-side
    all_users_raw = query_db('SELECT id, username, profile, created_at FROM users')
    all_users = []
    for user in all_users_raw:
        user_dict = dict(user)
        user_risk_score = user_risk_analysis(user_dict['id'])
        risk_label, risk_sort_key = get_risk_profile(user_risk_score)
        user_dict['risk_label'] = risk_label
        user_dict['risk_sort_key'] = risk_sort_key
        user_dict['risk_score'] = min(5.0, round(user_risk_score, 2))
        all_users.append(user_dict)

    all_users.sort(key=lambda x: x['risk_score'], reverse=True)
    total_users = len(all_users)
    users = all_users[users_offset : users_offset + PAGE_SIZE]
    total_users_pages = (total_users + PAGE_SIZE - 1) // PAGE_SIZE

    # --- Posts Tab Data ---
    posts_offset = (posts_page - 1) * PAGE_SIZE
    total_posts_count = query_db('SELECT COUNT(*) as count FROM posts', one=True)['count']
    total_posts_pages = (total_posts_count + PAGE_SIZE - 1) // PAGE_SIZE

    posts_raw = query_db(f'''
        SELECT p.id, p.content, p.created_at, u.username, u.created_at as user_created_at
        FROM posts p JOIN users u ON p.user_id = u.id
        ORDER BY p.id DESC -- Order by ID for consistent pagination before risk sort
        LIMIT ? OFFSET ?
    ''', (PAGE_SIZE, posts_offset))
    posts = []
    for post in posts_raw:
        post_dict = dict(post)
        _, base_score = moderate_content(post_dict['content'])
        final_score = base_score 
        author_created_dt = post_dict['user_created_at']
        author_age_days = (datetime.utcnow() - author_created_dt).days
        if author_age_days < 7:
            final_score *= 1.5
        risk_label, risk_sort_key = get_risk_profile(final_score)
        post_dict['risk_label'] = risk_label
        post_dict['risk_sort_key'] = risk_sort_key
        post_dict['risk_score'] = round(final_score, 2)
        posts.append(post_dict)

    posts.sort(key=lambda x: x['risk_score'], reverse=True) # Sort after fetching and scoring

    # --- Comments Tab Data ---
    comments_offset = (comments_page - 1) * PAGE_SIZE
    total_comments_count = query_db('SELECT COUNT(*) as count FROM comments', one=True)['count']
    total_comments_pages = (total_comments_count + PAGE_SIZE - 1) // PAGE_SIZE

    comments_raw = query_db(f'''
        SELECT c.id, c.content, c.created_at, u.username, u.created_at as user_created_at
        FROM comments c JOIN users u ON c.user_id = u.id
        ORDER BY c.id DESC -- Order by ID for consistent pagination before risk sort
        LIMIT ? OFFSET ?
    ''', (PAGE_SIZE, comments_offset))
    comments = []
    for comment in comments_raw:
        comment_dict = dict(comment)
        _, score = moderate_content(comment_dict['content'])
        author_created_dt = comment_dict['user_created_at']
        author_age_days = (datetime.utcnow() - author_created_dt).days
        if author_age_days < 7:
            score *= 1.5
        risk_label, risk_sort_key = get_risk_profile(score)
        comment_dict['risk_label'] = risk_label
        comment_dict['risk_sort_key'] = risk_sort_key
        comment_dict['risk_score'] = round(score, 2)
        comments.append(comment_dict)

    comments.sort(key=lambda x: x['risk_score'], reverse=True) # Sort after fetching and scoring


    return render_template('admin.html.j2', 
                           users=users, 
                           posts=posts, 
                           comments=comments,
                           
                           # Pagination for Users
                           users_page=users_page,
                           total_users_pages=total_users_pages,
                           users_has_next=(users_page < total_users_pages),
                           users_has_prev=(users_page > 1),

                           # Pagination for Posts
                           posts_page=posts_page,
                           total_posts_pages=total_posts_pages,
                           posts_has_next=(posts_page < total_posts_pages),
                           posts_has_prev=(posts_page > 1),

                           # Pagination for Comments
                           comments_page=comments_page,
                           total_comments_pages=total_comments_pages,
                           comments_has_next=(comments_page < total_comments_pages),
                           comments_has_prev=(comments_page > 1),

                           current_tab=current_tab,
                           PAGE_SIZE=PAGE_SIZE)



@app.route('/admin/delete/user/<int:user_id>', methods=['POST'])
def admin_delete_user(user_id):
    if session.get('username') != 'admin':
        flash("You do not have permission to perform this action.", "danger")
        return redirect(url_for('feed'))
        
    if user_id == session.get('user_id'):
        flash('You cannot delete your own account from the admin panel.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    db = get_db()
    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()
    flash(f'User {user_id} and all their content has been deleted.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete/post/<int:post_id>', methods=['POST'])
def admin_delete_post(post_id):
    if session.get('username') != 'admin':
        flash("You do not have permission to perform this action.", "danger")
        return redirect(url_for('feed'))

    db = get_db()
    db.execute('DELETE FROM comments WHERE post_id = ?', (post_id,))
    db.execute('DELETE FROM reactions WHERE post_id = ?', (post_id,))
    db.execute('DELETE FROM ask_requests WHERE post_id = ?', (post_id,))
    db.execute('UPDATE users SET pinned_post_id = NULL WHERE pinned_post_id = ?', (post_id,))
    db.execute('DELETE FROM posts WHERE id = ?', (post_id,))
    db.commit()
    flash(f'Post {post_id} has been deleted.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete/comment/<int:comment_id>', methods=['POST'])
def admin_delete_comment(comment_id):
    if session.get('username') != 'admin':
        flash("You do not have permission to perform this action.", "danger")
        return redirect(url_for('feed'))

    db = get_db()
    db.execute('DELETE FROM comments WHERE id = ?', (comment_id,))
    db.commit()
    flash(f'Comment {comment_id} has been deleted.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/rules')
def rules():
    return render_template('rules.html.j2')


# ----- Design Claim feature: "Ask someone" (Chapter 2, Design Claim 4)
# "Compared to broadcasting requirements for contribution to all community members,
# asking specific people to make contributions increases the likelihood that they will do so."
# Post authors can ask specific members to answer their post. The asked member gets a
# personal request that they can answer (by commenting) or decline. Suggestions of whom
# to ask are based on who has written about the same topics (Design Claim 3).

# Asking everyone would turn the request back into a broadcast, so the number is capped
MAX_ASKS_PER_POST = 3
SUGGESTION_COUNT = 5
# Hashtags say what a post is about more clearly than ordinary words, so they count double
HASHTAG_WEIGHT = 2

STOPWORDS = {
    'all', 'and', 'any', 'are', 'big', 'but', 'can', 'day', 'did', 'dont', 'end', 'far',
    'few', 'for', 'get', 'got', 'had', 'has', 'her', 'him', 'his', 'how', 'its', 'ive',
    'let', 'lot', 'man', 'may', 'new', 'not', 'now', 'off', 'old', 'one', 'our', 'out',
    'own', 'put', 'say', 'see', 'she', 'the', 'too', 'top', 'try', 'two', 'use', 'was',
    'way', 'who', 'why', 'yes', 'yet', 'you',
    'about', 'above', 'after', 'again', 'against', 'also', 'always', 'anyone', 'anything',
    'back', 'because', 'been', 'before', 'being', 'below', 'between', 'both', 'cannot',
    'could', 'does', 'doing', 'down', 'during', 'each', 'even', 'ever', 'every', 'everyone',
    'first', 'from', 'further', 'going', 'good', 'great', 'have', 'having', 'here', 'hers',
    'herself', 'himself', 'into', 'itself', 'just', 'know', 'last', 'like', 'made', 'make',
    'maybe', 'more', 'most', 'much', 'myself', 'need', 'never', 'only', 'other', 'ours',
    'over', 'people', 'really', 'same', 'should', 'some', 'someone', 'something', 'still',
    'such', 'than', 'that', 'their', 'theirs', 'them', 'themselves', 'then', 'there',
    'these', 'they', 'thing', 'things', 'think', 'this', 'those', 'through', 'time',
    'today', 'very', 'want', 'well', 'were', 'what', 'when', 'where', 'which', 'while',
    'will', 'with', 'would', 'year', 'your', 'yours', 'yourself',
}


def init_ask_requests_table():
    """Creates the table that stores 'Ask someone' requests, if it does not exist yet."""
    db = sqlite3.connect(DATABASE)
    db.execute('''
        CREATE TABLE IF NOT EXISTS ask_requests (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id      INTEGER NOT NULL,
            requester_id INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'pending', -- pending, answered or declined
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
            responded_at TIMESTAMP
        )
    ''')
    db.commit()
    db.close()

init_ask_requests_table()


def extract_keywords(text):
    """
    Returns the set of meaningful lowercase terms in a text: its hashtags (kept with
    their '#', e.g. '#diylife') and its other words of 3+ letters (e.g. 'diy', 'drill').
    """
    text = (text or '').lower()
    hashtags = {'#' + tag for tag in re.findall(r"#(\w+)", text)}
    words = re.findall(r"[a-z]+", re.sub(r"#\w+", ' ', text))
    return hashtags | {word for word in words if len(word) >= 3 and word not in STOPWORDS}


def shared_topics(post_terms, text_terms):
    """
    Returns the post's terms that also appear in another text. A hashtag also matches
    the same plain word ('#diy' and 'diy') and longer or shorter tags ('#diy' and '#diylife').
    """
    shared = post_terms & text_terms
    for term in post_terms - shared:
        if not term.startswith('#'):
            continue
        if term[1:] in text_terms:
            shared.add(term)
        elif any(other.startswith('#') and min(len(term), len(other)) > 3
                 and (other.startswith(term) or term.startswith(other))
                 for other in text_terms):
            shared.add(term)
    return shared


def suggest_people_to_ask(post, exclude_ids):
    """
    Suggests members who have written about the same topics as the post, i.e. people who
    are likely interested in it and able to answer (Design Claim 3).

    Each shared term is weighted by how rare it is on the platform, so a specific word
    like 'drill' counts for more than a common word like 'weekend'. Hashtags count double.

    Returns a list of dicts with the user_id, username and matching topics of each member.
    """
    keywords = extract_keywords(post['content'])
    if not keywords:
        return []

    texts = query_db('''
        SELECT user_id, content FROM posts WHERE id != ?
        UNION ALL
        SELECT user_id, content FROM comments
    ''', (post['id'],))

    doc_freq = collections.Counter()
    user_matches = collections.defaultdict(collections.Counter)
    for row in texts:
        for word in shared_topics(keywords, extract_keywords(row['content'])):
            doc_freq[word] += 1
            if row['user_id'] not in exclude_ids:
                user_matches[row['user_id']][word] += 1

    scores = {}
    for user_id, matches in user_matches.items():
        # Each term counts for at most 5 texts, so very active members don't win on volume alone
        scores[user_id] = sum(math.log(len(texts) / doc_freq[word]) * min(count, 5)
                              * (HASHTAG_WEIGHT if word.startswith('#') else 1)
                              for word, count in matches.items())

    suggestions = []
    for user_id in sorted(scores, key=scores.get, reverse=True)[:SUGGESTION_COUNT]:
        user = query_db('SELECT username FROM users WHERE id = ?', (user_id,), one=True)
        if user:
            # Show the rarest shared words first, they explain the suggestion best
            topics = sorted(user_matches[user_id], key=lambda word: doc_freq[word])[:3]
            suggestions.append({'user_id': user_id, 'username': user['username'], 'topics': topics})
    return suggestions


def get_ask_context(post, current_user_id):
    """Collects the 'Ask someone' data that the post detail page needs."""
    context = {
        'my_ask_request': None,
        'sent_asks': [],
        'ask_suggestions': [],
        'ask_usernames': [],
        'max_asks': MAX_ASKS_PER_POST,
    }
    if not current_user_id:
        return context

    if current_user_id == post['user_id']:
        # The author sees who they already asked, and suggestions of whom to ask next
        context['sent_asks'] = query_db('''
            SELECT ar.status, u.id AS user_id, u.username
            FROM ask_requests ar
            JOIN users u ON u.id = ar.recipient_id
            WHERE ar.post_id = ?
            ORDER BY ar.created_at
        ''', (post['id'],))

        if len(context['sent_asks']) < MAX_ASKS_PER_POST:
            commenters = query_db('SELECT DISTINCT user_id FROM comments WHERE post_id = ?', (post['id'],))
            # No point suggesting the author, people already asked, or people who already answered
            exclude_ids = ({current_user_id}
                           | {ask['user_id'] for ask in context['sent_asks']}
                           | {row['user_id'] for row in commenters})
            context['ask_suggestions'] = suggest_people_to_ask(post, exclude_ids)
            usernames = query_db('SELECT username FROM users WHERE id != ? ORDER BY username', (current_user_id,))
            context['ask_usernames'] = [row['username'] for row in usernames]
    else:
        # Anyone else sees the request if this post's author asked them personally
        context['my_ask_request'] = query_db('''
            SELECT ar.id, u.username AS requester
            FROM ask_requests ar
            JOIN users u ON u.id = ar.requester_id
            WHERE ar.post_id = ? AND ar.recipient_id = ? AND ar.status = 'pending'
        ''', (post['id'], current_user_id), one=True)

    return context


@app.context_processor
def inject_pending_requests_count():
    """Makes the number of open requests for the logged-in user available in every template (navbar badge)."""
    user_id = session.get('user_id')
    if not user_id:
        return {}
    row = query_db("SELECT COUNT(*) AS cnt FROM ask_requests WHERE recipient_id = ? AND status = 'pending'",
                   (user_id,), one=True)
    return {'pending_requests_count': row['cnt'] if row else 0}


@app.route('/posts/<int:post_id>/ask', methods=['POST'])
def ask_someone(post_id):
    """Sends a personal request to a specific member to answer the current user's post."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to ask someone.', 'danger')
        return redirect(url_for('login'))

    # Only the author of a post can ask people to answer it
    post = query_db('SELECT id, user_id FROM posts WHERE id = ?', (post_id,), one=True)
    if not post or post['user_id'] != user_id:
        flash('You can only ask people to answer your own posts.', 'danger')
        return redirect(url_for('feed'))

    username = (request.form.get('username') or '').strip().lstrip('@')
    recipient = query_db('SELECT id, username FROM users WHERE username = ?', (username,), one=True)
    asked_count = query_db('SELECT COUNT(*) AS cnt FROM ask_requests WHERE post_id = ?', (post_id,), one=True)['cnt']

    if not recipient:
        flash(f'No member called "{username}" was found.', 'warning')
    elif recipient['id'] == user_id:
        flash('You cannot ask yourself.', 'warning')
    elif query_db('SELECT 1 FROM ask_requests WHERE post_id = ? AND recipient_id = ?',
                  (post_id, recipient['id']), one=True):
        flash(f"You already asked @{recipient['username']} about this post.", 'info')
    elif asked_count >= MAX_ASKS_PER_POST:
        flash(f'You can ask at most {MAX_ASKS_PER_POST} people per post.', 'warning')
    else:
        db = get_db()
        db.execute('INSERT INTO ask_requests (post_id, requester_id, recipient_id) VALUES (?, ?, ?)',
                   (post_id, user_id, recipient['id']))
        db.commit()
        flash(f"You asked @{recipient['username']} to answer your post.", 'success')

    return redirect(url_for('post_detail', post_id=post_id))


@app.route('/requests')
def requests_inbox():
    """Shows the requests other members sent to the current user, and the requests they sent."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to see your requests.', 'danger')
        return redirect(url_for('login'))

    # Open requests first, then the most recent ones
    received_raw = query_db('''
        SELECT ar.id, ar.post_id, ar.status, ar.created_at, u.username AS requester, p.content
        FROM ask_requests ar
        JOIN users u ON u.id = ar.requester_id
        JOIN posts p ON p.id = ar.post_id
        WHERE ar.recipient_id = ?
        ORDER BY ar.status = 'pending' DESC, ar.created_at DESC
        LIMIT 50
    ''', (user_id,))

    sent_raw = query_db('''
        SELECT ar.id, ar.post_id, ar.status, ar.created_at, u.username AS recipient, p.content
        FROM ask_requests ar
        JOIN users u ON u.id = ar.recipient_id
        JOIN posts p ON p.id = ar.post_id
        WHERE ar.requester_id = ?
        ORDER BY ar.created_at DESC
        LIMIT 50
    ''', (user_id,))

    received = []
    for row in received_raw:
        item = dict(row)
        item['content'], _ = moderate_content(item['content'])
        received.append(item)

    sent = []
    for row in sent_raw:
        item = dict(row)
        item['content'], _ = moderate_content(item['content'])
        sent.append(item)

    return render_template('requests.html.j2', received=received, sent=sent)


@app.route('/requests/<int:request_id>/decline', methods=['POST'])
def decline_request(request_id):
    """Lets the asked member decline a request, so the author knows to ask someone else."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to decline a request.', 'danger')
        return redirect(url_for('login'))

    # Only the asked member can decline, and only while the request is still open
    db = get_db()
    cur = db.execute('''
        UPDATE ask_requests SET status = 'declined', responded_at = CURRENT_TIMESTAMP
        WHERE id = ? AND recipient_id = ? AND status = 'pending'
    ''', (request_id, user_id))
    db.commit()

    if cur.rowcount > 0:
        flash('Request declined. The member who asked you can now ask someone else.', 'info')
    else:
        flash('Request not found.', 'warning')

    return redirect(request.referrer or url_for('requests_inbox'))

# ----- Design Claim feature: Reputation levels (Chapter 2, Design Claim 23)
# "Rewards--whether in the form of status, privileges, or material benefits--motivate contributions."
# Members earn reputation points when others react to or comment on their posts, and higher
# levels unlock privileges: Level 2 can pin a post to the top of their profile, Level 3 can
# choose their own avatar colour, which everyone sees next to their name on every post and comment.
# Points depend on how others respond, not on how much you post (performance-contingent), and one
# member can only give you a limited number of points. Both make the system harder to game
# (Design Claims 25-27).

POINTS_PER_REACTION = 1
POINTS_PER_COMMENT = 2
# A single member (e.g. a friend) can't push someone up the levels on their own
MAX_POINTS_PER_MEMBER = 10
# TEMPORARY, for testing and screenshots: extra points for every member. Set back to 0 before submitting.
TEST_BONUS_POINTS = 30

LEVELS = [
    {'level': 1, 'name': 'Newcomer', 'min_points': 0, 'privilege': None},
    {'level': 2, 'name': 'Contributor', 'min_points': 25, 'privilege': 'Pin a post to the top of your profile'},
    {'level': 3, 'name': 'Valued member', 'min_points': 100, 'privilege': 'Choose your own avatar colour'},
]
PIN_LEVEL = 2
AVATAR_COLOR_LEVEL = 3

# A fixed palette instead of a free colour picker, so every name stays readable on the light background
AVATAR_COLORS = {
    'Red': '#c62828', 'Coral': '#e4572e', 'Orange': '#d97706', 'Gold': '#b08900',
    'Green': '#2e7d32', 'Teal': '#00897b', 'Blue': '#1d4ed8', 'Ocean': '#0277bd',
    'Purple': '#6a1b9a', 'Magenta': '#ad1457', 'Brown': '#6d4c41', 'Slate': '#455a64',
}


def init_reputation_columns():
    """Adds the columns for the Level 2 and Level 3 privileges to the users table, if missing."""
    db = sqlite3.connect(DATABASE)
    columns = {row[1] for row in db.execute('PRAGMA table_info(users)')}
    if 'pinned_post_id' not in columns:
        db.execute('ALTER TABLE users ADD COLUMN pinned_post_id INTEGER DEFAULT NULL')
    if 'avatar_color' not in columns:
        db.execute('ALTER TABLE users ADD COLUMN avatar_color TEXT DEFAULT NULL')
    db.commit()
    db.close()

init_reputation_columns()


def get_reputation(user_id):
    """
    Returns a member's reputation points: points for every reaction and comment that other
    members left on their posts, counting at most MAX_POINTS_PER_MEMBER from any single member.
    Reactions and comments on your own posts don't count.
    """
    row = query_db('''
        SELECT IFNULL(SUM(MIN(points, ?)), 0) AS total FROM (
            SELECT giver_id, SUM(points) AS points FROM (
                SELECT r.user_id AS giver_id, ? AS points
                FROM reactions r JOIN posts p ON p.id = r.post_id
                WHERE p.user_id = ? AND r.user_id != ?
                UNION ALL
                SELECT c.user_id AS giver_id, ? AS points
                FROM comments c JOIN posts p ON p.id = c.post_id
                WHERE p.user_id = ? AND c.user_id != ?
            )
            GROUP BY giver_id
        )
    ''', (MAX_POINTS_PER_MEMBER, POINTS_PER_REACTION, user_id, user_id,
          POINTS_PER_COMMENT, user_id, user_id), one=True)
    return (row['total'] if row else 0) + TEST_BONUS_POINTS


def get_level(user_id):
    """Returns a member's points, current level and progress towards the next level."""
    points = get_reputation(user_id)
    current = [level for level in LEVELS if points >= level['min_points']][-1]
    next_level = next((level for level in LEVELS if level['min_points'] > points), None)

    progress = 100
    if next_level:
        span = next_level['min_points'] - current['min_points']
        progress = int((points - current['min_points']) / span * 100)

    return {
        'points': points,
        'level': current['level'],
        'name': current['name'],
        'next': next_level,
        'points_to_next': next_level['min_points'] - points if next_level else 0,
        'progress': progress,
    }


def get_custom_avatar_colors():
    """Returns {username: colour} for members who chose their own avatar colour, loaded once per request."""
    if 'avatar_colors' not in g:
        rows = query_db('SELECT username, avatar_color FROM users WHERE avatar_color IS NOT NULL') or []
        g.avatar_colors = {row['username']: row['avatar_color'] for row in rows}
    return g.avatar_colors


@app.route('/posts/<int:post_id>/pin', methods=['POST'])
def pin_post(post_id):
    """Level 2 privilege: pins one of the current user's posts to the top of their profile."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to pin a post.', 'danger')
        return redirect(url_for('login'))

    post = query_db('SELECT id, user_id FROM posts WHERE id = ?', (post_id,), one=True)
    if not post or post['user_id'] != user_id:
        flash('You can only pin your own posts.', 'danger')
    elif get_level(user_id)['level'] < PIN_LEVEL:
        flash(f'Reach Level {PIN_LEVEL} to unlock pinning posts.', 'warning')
    else:
        db = get_db()
        db.execute('UPDATE users SET pinned_post_id = ? WHERE id = ?', (post_id, user_id))
        db.commit()
        flash('Post pinned to the top of your profile.', 'success')

    return redirect(url_for('user_profile', username=session.get('username')))


@app.route('/profile/unpin', methods=['POST'])
def unpin_post():
    """Removes the pinned post from the current user's profile."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to unpin a post.', 'danger')
        return redirect(url_for('login'))

    db = get_db()
    db.execute('UPDATE users SET pinned_post_id = NULL WHERE id = ?', (user_id,))
    db.commit()
    flash('Post unpinned.', 'info')
    return redirect(url_for('user_profile', username=session.get('username')))


@app.route('/profile/avatar-color', methods=['POST'])
def set_avatar_color():
    """Level 3 privilege: lets the current user choose their own avatar colour from the palette."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to change your avatar colour.', 'danger')
        return redirect(url_for('login'))

    color = request.form.get('color')
    db = get_db()

    if request.form.get('reset'):
        # Going back to the automatic colour is always allowed
        db.execute('UPDATE users SET avatar_color = NULL WHERE id = ?', (user_id,))
        db.commit()
        flash('Your avatar uses the automatic colour again.', 'info')
    elif get_level(user_id)['level'] < AVATAR_COLOR_LEVEL:
        flash(f'Reach Level {AVATAR_COLOR_LEVEL} to unlock choosing your avatar colour.', 'warning')
    elif color not in AVATAR_COLORS.values():
        flash('Please pick one of the colours.', 'warning')
    else:
        db.execute('UPDATE users SET avatar_color = ? WHERE id = ?', (color, user_id))
        db.commit()
        flash('Your new avatar colour is saved. Everyone will see it next to your name.', 'success')

    return redirect(url_for('user_profile', username=session.get('username')))


@app.template_global()
def loop_color(user_id):
    # Level 3 members can choose their own colour (see the reputation feature above)
    custom_color = get_custom_avatar_colors().get(user_id)
    if custom_color:
        return custom_color

    # Generate a pastel color based on user_id hash
    h = hashlib.md5(str(user_id).encode()).hexdigest()
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return f'rgb({r % 128 + 80}, {g % 128 + 80}, {b % 128 + 80})'


# ----- Design Claim feature: "People you may know" (Chapter 3, Design Claim 14)
# "Facilitating interaction with 'friends of friends' can enhance bonds-based commitment."
# Members see people who are followed by the people they follow, together with the shared
# connections, and can follow them or mark them as "Not interested".
# Members who don't follow anyone yet have no friends of friends, so they get popular members instead.

SUGGESTIONS_ON_FEED = 3
SUGGESTIONS_ON_PAGE = 20
# How many shared connections are named, e.g. "Followed by @a, @b and 12 others you follow"
MUTUALS_NAMED = 2


def init_dismissed_suggestions_table():
    """Creates the table that remembers who a member marked as 'Not interested', if it does not exist yet."""
    db = sqlite3.connect(DATABASE)
    db.execute('''
        CREATE TABLE IF NOT EXISTS dismissed_suggestions (
            user_id           INTEGER NOT NULL,
            dismissed_user_id INTEGER NOT NULL,
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
            PRIMARY KEY (user_id, dismissed_user_id)
        )
    ''')
    db.commit()
    db.close()

init_dismissed_suggestions_table()


def describe_mutuals(names, count):
    """Builds the 'Followed by ...' text that explains why someone is suggested."""
    named = ['@' + name for name in names]
    others = count - len(named)
    if others > 0:
        return f"Followed by {', '.join(named)} and {others} other{'s' if others != 1 else ''} you follow"
    if len(named) == 1:
        return f'Followed by {named[0]}, who you follow'
    return f"Followed by {' and '.join(named)}, who you follow"


def get_friends_of_friends(user_id, limit):
    """
    Returns members followed by the people the user follows, ranked by how many of the user's
    connections follow them (people who follow the user back come first on a tie).
    Excludes the user, people they already follow and people they marked as not interested.
    """
    rows = query_db('''
        SELECT u.id AS user_id, u.username,
               COUNT(DISTINCT friend.id) AS mutual_count,
               EXISTS (SELECT 1 FROM follows WHERE follower_id = u.id AND followed_id = ?) AS follows_you
        FROM follows f1
        JOIN users friend ON friend.id = f1.followed_id
        JOIN follows f2 ON f2.follower_id = friend.id
        JOIN users u ON u.id = f2.followed_id
        WHERE f1.follower_id = ?
          AND u.id != ?
          AND u.id NOT IN (SELECT followed_id FROM follows WHERE follower_id = ?)
          AND u.id NOT IN (SELECT dismissed_user_id FROM dismissed_suggestions WHERE user_id = ?)
        GROUP BY u.id
        ORDER BY mutual_count DESC, follows_you DESC, u.username
        LIMIT ?
    ''', (user_id, user_id, user_id, user_id, user_id, limit)) or []

    people = []
    for row in rows:
        person = dict(row)
        # Name a few of the shared connections, they make the suggestion personal
        mutuals = query_db('''
            SELECT DISTINCT friend.username
            FROM follows f1
            JOIN users friend ON friend.id = f1.followed_id
            JOIN follows f2 ON f2.follower_id = friend.id
            WHERE f1.follower_id = ? AND f2.followed_id = ?
            ORDER BY friend.username
            LIMIT ?
        ''', (user_id, person['user_id'], MUTUALS_NAMED)) or []
        person['reason'] = describe_mutuals([m['username'] for m in mutuals], person['mutual_count'])
        people.append(person)
    return people


def get_popular_members(user_id, limit):
    """Fallback for members without friends of friends: the most-followed members they don't follow yet."""
    rows = query_db('''
        SELECT u.id AS user_id, u.username,
               COUNT(DISTINCT f.follower_id) AS follower_count,
               EXISTS (SELECT 1 FROM follows WHERE follower_id = u.id AND followed_id = ?) AS follows_you
        FROM users u
        JOIN follows f ON f.followed_id = u.id
        WHERE u.id != ?
          AND u.id NOT IN (SELECT followed_id FROM follows WHERE follower_id = ?)
          AND u.id NOT IN (SELECT dismissed_user_id FROM dismissed_suggestions WHERE user_id = ?)
        GROUP BY u.id
        ORDER BY follower_count DESC, u.username
        LIMIT ?
    ''', (user_id, user_id, user_id, user_id, limit)) or []

    people = []
    for row in rows:
        person = dict(row)
        person['reason'] = f"Popular member, followed by {person['follower_count']} people"
        people.append(person)
    return people


def get_people_suggestions(user_id, limit):
    """Returns {'kind': 'friends_of_friends' or 'popular', 'people': [...]} for the given member."""
    people = get_friends_of_friends(user_id, limit)
    if people:
        return {'kind': 'friends_of_friends', 'people': people}
    return {'kind': 'popular', 'people': get_popular_members(user_id, limit)}


@app.route('/people')
def people_you_may_know():
    """Shows the full list of people the current user may know."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to see people you may know.', 'danger')
        return redirect(url_for('login'))

    hidden_count = query_db('SELECT COUNT(*) AS cnt FROM dismissed_suggestions WHERE user_id = ?',
                            (user_id,), one=True)['cnt']
    return render_template('people.html.j2',
                           suggestions=get_people_suggestions(user_id, SUGGESTIONS_ON_PAGE),
                           hidden_count=hidden_count)


@app.route('/people/<int:user_id>/dismiss', methods=['POST'])
def dismiss_suggestion(user_id):
    """Hides a suggested member: they won't be suggested to the current user again."""
    current_user_id = session.get('user_id')

    # Block access if user is not logged in
    if not current_user_id:
        flash('You must be logged in to hide suggestions.', 'danger')
        return redirect(url_for('login'))

    person = query_db('SELECT username FROM users WHERE id = ?', (user_id,), one=True)
    if not person or user_id == current_user_id:
        flash('Suggestion not found.', 'warning')
        return redirect(request.referrer or url_for('people_you_may_know'))

    db = get_db()
    db.execute('INSERT OR IGNORE INTO dismissed_suggestions (user_id, dismissed_user_id) VALUES (?, ?)',
               (current_user_id, user_id))
    db.commit()
    flash(f"Got it, we won't suggest @{person['username']} again.", 'info')
    return redirect(request.referrer or url_for('people_you_may_know'))


@app.route('/people/reset', methods=['POST'])
def reset_dismissed_suggestions():
    """Brings back everyone the current user marked as 'Not interested'."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to do this.', 'danger')
        return redirect(url_for('login'))

    db = get_db()
    db.execute('DELETE FROM dismissed_suggestions WHERE user_id = ?', (user_id,))
    db.commit()
    flash('Hidden people can be suggested again.', 'info')
    return redirect(url_for('people_you_may_know'))


# ----- Functions to be implemented are below
# Coding Assignment #2

# Assignment 2.2
def user_risk_analysis(user_id):
    """
    Args:
        user_id: The ID of the user on which we perform risk analysis.

    Returns:
        A float number score showing the risk associated with this user. There are no strict rules or bounds to this score, other than that a score of less than 1.0 means no risk, 1.0 to 3.0 is low risk, 3.0 to 5.0 is medium risk and above 5.0 is high risk. (An upper bound of 5.0 is applied to this score elsewhere in the codebase) 
        
        You will be able to check the scores by logging in with the administrator account:
            username: admin
            password: admin
        Then, navigate to the /admin endpoint. (http://localhost:8081/admin)
    """
    
    score = 0

    return score;

    
# Assignment 2.1
def moderate_content(content):
    """
    Args
        content: the text content of a post or comment to be moderated.
        
    Returns: 
        A tuple containing the moderated content (string) and a severity score (float). There are no strict rules or bounds to the severity score, other than that a score of less than 1.0 means no risk, 1.0 to 3.0 is low risk, 3.0 to 5.0 is medium risk and above 5.0 is high risk.
    
    This function moderates a string of content and calculates a severity score based on
    rules loaded from the 'censorship.dat' file. These are already loaded as TIER1_WORDS, TIER2_PHRASES and TIER3_WORDS. Tier 1 corresponds to strong profanity, Tier 2 to scam/spam phrases and Tier 3 to mild profanity.
    
    You will be able to check the scores by logging in with the administrator account:
            username: admin
            password: admin
    Then, navigate to the /admin endpoint. (http://localhost:8081/admin)
    """

    moderated_content = content
    score = 0
    
    return moderated_content, score

# Coding Assignment #3
# Assignment 3.1
def recommend(user_id, filter_following):
    """
    Args:
        user_id: The ID of the current user.
        filter_following: Boolean, True if we only want to see recommendations from followed users.

    Returns:
        A list of 5 recommended posts, in reverse-chronological order.

    To test whether your recommendation algorithm works, let's pretend we like the DIY topic. Here are some users that often post DIY comment and a few example posts. Make sure your account did not engage with anything else. You should test your algorithm with these and see if your recommendation algorithm picks up on your interest in DIY and starts showing related content.
    
    Users: @starboy99, @DancingDolphin, @blogger_bob
    Posts: 1810, 1875, 1880, 2113
    
    Materials: 
    - https://www.nvidia.com/en-us/glossary/recommendation-system/
    - http://www.configworks.com/mz/handout_recsys_sac2010.pdf
    - https://www.researchgate.net/publication/227268858_Recommender_Systems_Handbook
    """

    recommended_posts = {} 

    return recommended_posts;

if __name__ == '__main__':
    app.run(debug=True, port=8081)

