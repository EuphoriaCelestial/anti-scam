# QuizMaster

A full-stack quiz application with Python/Flask backend, PostgreSQL database, file storage, and a modern single-page frontend.

---

## Project Structure

```
quizapp/
├── app.py                  # Flask backend (all APIs)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── sample_questions.csv    # Sample CSV to test question upload
├── uploads/
│   ├── pdf/                # Uploaded PDF files
│   └── video/              # Uploaded video files
└── static/
    └── index.html          # Single-page frontend
```

---

## Quick Start (with SQLite — no Postgres needed)

```bash
# 1. Install dependencies
pip install flask PyJWT Werkzeug pandas

# 2. Run
python app.py
```

Open http://localhost:5000

**Default admin login:** username `admin`, password `admin123`

---

## Quick Start (with Docker + PostgreSQL)

```bash
docker-compose up --build
```

Open http://localhost:8080

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(empty = SQLite)* | PostgreSQL connection string, e.g. `postgresql://user:pass@host:5432/dbname` |
| `SECRET_KEY` | `dev-secret-change-in-prod` | JWT signing secret |

---

## API Reference

### Auth
| Method | Path | Description |
|---|---|---|
| POST | `/api/register` | Register player `{name, phone, address, password}` |
| POST | `/api/login` | Login `{username, password}` → `{token, role, name}` |
| POST | `/api/logout` | Logout (client discards token) |

### Questions (Admin only for write, Player for read)
| Method | Path | Description |
|---|---|---|
| GET | `/api/questions` | Get all questions |
| GET | `/api/questions/random` | Get 10 random questions with shuffled answers |
| POST | `/api/questions` | Add one question |
| PUT | `/api/questions/<id>` | Update one question |
| DELETE | `/api/questions/<id>` | Delete one question |
| DELETE | `/api/questions/all` | Delete all questions |
| POST | `/api/questions/upload` | Upload CSV file of questions |

### High Scores
| Method | Path | Description |
|---|---|---|
| GET | `/api/highscores` | Get top 20 scores |
| POST | `/api/highscores` | Save score `{score, time_seconds}` |

### Files
| Method | Path | Description |
|---|---|---|
| GET | `/api/files` | List all uploaded files |
| POST | `/api/upload/pdf` | Upload PDF (admin) |
| POST | `/api/upload/video` | Upload video (admin) |
| DELETE | `/api/files/<id>` | Delete file (admin) |
| GET | `/uploads/pdf/<filename>` | Serve PDF |
| GET | `/uploads/video/<filename>` | Serve video |

---

## CSV Format

```csv
question,correct_answer,wrong_answer_1,wrong_answer_2,wrong_answer_3
What is the capital of France?,Paris,London,Berlin,Madrid
```

First column = question, second = correct answer, columns 3–5 = wrong answers.

---

## Screens

| Screen | Description |
|---|---|
| Login / Register | Phone + password for players; username + password for admin |
| Admin Dashboard | Manage questions and files side by side |
| Home | Menu to start game, view scores, or browse library |
| Quiz | 10 random questions, one by one, with timer |
| High Scores | Top 20 leaderboard |
| Library | Browse and view PDFs / watch videos |
