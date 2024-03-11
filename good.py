import aiohttp
import asyncio
import os
import traceback

from flask import Flask, session, render_template, request, redirect, url_for, jsonify
from flask_session import Session
from sqlalchemy import create_engine, text
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Row  # Import Row from SQLAlchemy

from werkzeug.security import generate_password_hash, check_password_hash


app = Flask(__name__)

# Configure session to use filesystem
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Set up database with username and password
engine = create_engine("postgresql://postgres:0513@localhost/Book_Reviews")
db = scoped_session(sessionmaker(bind=engine))

# Define function to asynchronously fetch data from Google Books API
async def get_google_book_info_async(isbn):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}") as response:
                data = await response.json()
                return data
    except aiohttp.ClientConnectionError as e:
        print(f"Error while fetching book details from Google Books API: {e}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None

@app.route("/")
def index():
    return render_template("landing_page.html")

@app.route("/search", methods=["GET", "POST"])
def search():
    search_query = request.form.get("search_query", "")

    if request.method == "POST":
        try:
            session['search_query'] = search_query
            app.logger.info(f"Search Query: {search_query}")
            query = text("SELECT * FROM books_csv WHERE title ILIKE :query OR author ILIKE :query OR isbn ILIKE :query")
            query = query.bindparams(query=f"%{search_query}%")
            app.logger.info(f"SQL Query: {str(query)}")
            books = db.execute(query).fetchall()
            app.logger.info(f"Number of Results: {len(books)}")
            app.logger.info(f"Retrieved Books: {books}")

            search_results = []

            for book in books:
                isbn = book.isbn
                google_book_info = asyncio.run(get_google_book_info_async(isbn))

                if google_book_info and 'items' in google_book_info and len(google_book_info['items']) > 0:
                    volume_info = google_book_info['items'][0]['volumeInfo']
                    average_rating = volume_info.get('averageRating')
                    ratings_count = volume_info.get('ratingsCount')

                    book_dict = {
                        'isbn': isbn,
                        'title': book.title,
                        'author': book.author,
                        'year': book.year,
                        'average_rating': average_rating if average_rating is not None else "N/A",
                        'ratings_count': ratings_count if ratings_count is not None else "N/A"
                    }

                    search_results.append(book_dict)
                else:
                    app.logger.error(f"No data found from Google Books API for ISBN: {isbn}")

            return render_template("search_results.html", books=search_results, search_query=search_query)

        except Exception as e:
            app.logger.error(f"Error during search: {e}")
            app.logger.error(traceback.format_exc())
            return render_template("detailed_error.html", message=f"An error occurred during search: {str(e)}", traceback=traceback.format_exc())

    return render_template("search_form.html", search_query=session.get('search_query', ''))


@app.route("/submit_review_from_search/<string:isbn>", methods=["POST"])
def submit_review_from_search(isbn):
    try:
        # Get form data
        rating = int(request.form.get("rating"))
        comment = request.form.get("comment")

        # Get user ID from the session
        user_id = session.get("user_id")

        # Check if the user has already submitted a review for this book
        existing_review = db.execute(
            text("SELECT * FROM book_reviews WHERE isbn = :isbn AND user_id = :user_id"),
            {"isbn": isbn, "user_id": user_id}
        ).fetchone()

        if existing_review:
            return render_template("error.html", message="You have already submitted a review for this book.")

        # Insert the review into the database
        db.execute(
            text("INSERT INTO book_reviews (isbn, user_id, rating, comment) VALUES (:isbn, :user_id, :rating, :comment)"),
            {"isbn": isbn, "user_id": user_id, "rating": rating, "comment": comment}
        )
        db.commit()

        return redirect(url_for('search'))

    except IntegrityError as e:
        # Handle unique constraint violation
        db.rollback()  # Roll back the transaction
        return render_template("error.html", message="You have already submitted a review for this book.")

    except Exception as e:
        # Log the error message for debugging
        app.logger.error(f"Error while submitting review from search: {e}")

        # Render a more detailed error template with the traceback
        return render_template("detailed_error.html", message="An error occurred while submitting the review from search. Please try again.")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            username = request.form.get("username")
            password = request.form.get("password")
            user = db.execute(text("SELECT * FROM users WHERE username = :username"), {"username": username}).fetchone()

            if user and check_password_hash(user[2], password):
                session["user_id"] = user[0]
                return redirect(url_for('search'))

            return render_template("login.html", error="Invalid username or password.")

        except Exception as e:
            app.logger.error(f"Error during login: {e}")
            return render_template("detailed_error.html", message="An error occurred during login. Please try again.", traceback=traceback.format_exc())

    return render_template("login.html")

@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        try:
            username = request.form.get("username")
            password = request.form.get("password")
            hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
            existing_user = db.execute(text("SELECT * FROM users WHERE LOWER(username) = LOWER(:username)"), {"username": username}).fetchone()

            if existing_user:
                return render_template("error.html", message="Username already exists. Please choose another.")

            db.execute(text("INSERT INTO users (username, password) VALUES (:username, :password)"),
                       {"username": username, "password": hashed_password})
            db.commit()

            return redirect(url_for('login'))

        except Exception as e:
            app.logger.error(f"Error during registration: {type(e).__name__} - {e}")
            return render_template("detailed_error.html", message=f"An error occurred during registration: {str(e)}", traceback=traceback.format_exc())

    return render_template("register.html")

@app.route("/view_reviews/<string:isbn>")
def view_reviews(isbn):
    try:
        reviews = db.execute(text("SELECT * FROM book_reviews WHERE isbn = :isbn"), {"isbn": isbn}).fetchall()
        
        if reviews:
            return render_template("reviews.html", reviews=reviews)
        else:
            return render_template("error.html", message="No reviews found for this book.")
    except Exception as e:
        app.logger.error(f"Error while retrieving reviews: {e}")
        return render_template("error.html", message="An error occurred while retrieving reviews.")


@app.route("/book/<string:isbn>", methods=["GET", "POST"])
def book(isbn):
    if request.method == "POST":
        try:
            # Handle review submission
            rating = int(request.form.get("rating"))
            comment = request.form.get("comment")
            user_id = session.get("user_id")

            existing_review = db.execute(
                text("SELECT * FROM book_reviews WHERE isbn = :isbn AND user_id = :user_id"),
                {"isbn": isbn, "user_id": user_id}
            ).fetchone()

            if existing_review:
                return render_template("error.html", message="You have already submitted a review for this book.")

            db.execute(
                text("INSERT INTO book_reviews (isbn, user_id, rating, comment, created_at) VALUES (:isbn, :user_id, :rating, :comment, :created_at)"),
                {"isbn": isbn, "user_id": user_id, "rating": rating, "comment": comment, "created_at": datetime.now()}
            )
            db.commit()

            return redirect(url_for('book', isbn=isbn))

        except Exception as e:
            app.logger.error(f"Error during review submission: {e}")
            app.logger.error(traceback.format_exc())
            return render_template("detailed_error.html", message="An error occurred during review submission.", traceback=traceback.format_exc())

    else:
        try:
            # Retrieve book details and reviews
            book_query = text("SELECT isbn, title, author, year FROM books_csv WHERE isbn = :isbn")
            book_query = book_query.bindparams(isbn=isbn)
            book = db.execute(book_query).fetchone()

            if book:
                reviews_query = text("SELECT * FROM book_reviews WHERE isbn = :isbn")
                reviews_query = reviews_query.bindparams(isbn=isbn)
                reviews = db.execute(reviews_query).fetchall()

                # Fetch additional information from Google Books API
                async def fetch_book_info():
                    return await get_google_book_info_async(isbn)

                google_book_info = asyncio.run(fetch_book_info())

                average_rating = "N/A"
                published_date = book[3]  # 'year' is at index 3 in the result tuple

                if google_book_info and 'items' in google_book_info and len(google_book_info['items']) > 0:
                    volume_info = google_book_info['items'][0]['volumeInfo']
                    average_rating = volume_info.get('averageRating', 'N/A')
                    if 'publishedDate' in volume_info:
                        published_date = volume_info['publishedDate'][:4]  # Extract the published year
                else:
                    app.logger.error("No data found from Google Books API")

                # Include the 'year' value in the book dictionary
                book_dict = {
                    'isbn': book.isbn,
                    'title': book.title,
                    'author': book.author,
                    'year': book.year,  # Include the 'year' value
                    'average_rating': average_rating,
                    'published_date': published_date,
                }

                return render_template("book_details.html", book=book_dict, reviews=reviews)

            else:
                return render_template("error.html", message="Book not found.")

        except Exception as e:
            app.logger.error(f"Error during book page request: {e}")
            app.logger.error(traceback.format_exc())
            return render_template("detailed_error.html", message="An error occurred while fetching book details.", traceback=traceback.format_exc())

from flask import request

@app.route("/api/<string:isbn>", methods=["GET"])
def api_book(isbn):
    if request.method == "GET":
        try:
            # Query database for book details
            book_query = text("SELECT * FROM books_csv WHERE isbn = :isbn")
            book_query = book_query.bindparams(isbn=isbn)
            book = db.execute(book_query).fetchone()

            if book:
                # Retrieve reviews count for the book
                reviews_count_query = text("SELECT COUNT(*) FROM book_reviews WHERE isbn = :isbn")
                reviews_count_query = reviews_count_query.bindparams(isbn=isbn)
                reviews_count = db.execute(reviews_count_query).fetchone()[0]

                # Calculate average rating
                average_rating_query = text("SELECT AVG(rating) FROM book_reviews WHERE isbn = :isbn")
                average_rating_query = average_rating_query.bindparams(isbn=isbn)
                average_rating = db.execute(average_rating_query).fetchone()[0]

                # If no reviews exist, set average rating and ratings count to 0
                if reviews_count == 0:
                    average_rating = 0
                    reviews_count = 0

                # Format response data as JSON
                response_data = {
                    "title": book.title,
                    "author": book.author,
                    "publishedDate": str(book.year),  # Assuming 'year' field holds the published date
                    "ISBN": book.isbn,
                    "reviewCount": reviews_count,
                    "averageRating": float(average_rating)
                }
                return jsonify(response_data)

            else:
                return jsonify({"error": "Book not found."}), 404

        except Exception as e:
            app.logger.error(f"Error during API request: {e}")
            return jsonify({"error": "An error occurred."}), 500
    else:
        return jsonify({"error": "Invalid request method."}), 405







if __name__ == "__main__":
    app.run(debug=True)

