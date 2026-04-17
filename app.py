import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from ai import adapt_content, chat_with_context
from auth import verify_password
from database import get_conn, init_db

BASE_DIR = Path(__file__).parent
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-please-change")

init_db()

app = FastAPI(title="Оқу көмекшісі")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def current_user(request: Request) -> Optional[dict]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, role FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if not row:
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def require_admin(request: Request):
    user = current_user(request)
    if not user or user["role"] != "admin":
        return None
    return user


def require_student(request: Request):
    user = current_user(request)
    if not user or user["role"] != "student":
        return None
    return user


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if user["role"] == "admin":
        return RedirectResponse("/admin", status_code=302)
    return RedirectResponse("/student", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "login.html", {"user": None, "error": None}
    )


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, password_hash, role FROM users WHERE username=?",
            (username.strip(),),
        ).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "user": None,
                "error": "Қате пайдаланушы аты немесе құпия сөз",
            },
            status_code=401,
        )
    request.session["user_id"] = row["id"]
    return RedirectResponse("/", status_code=302)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# -------- Admin --------
@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    with get_conn() as conn:
        materials = conn.execute(
            "SELECT id, title, content, created_at FROM materials ORDER BY created_at DESC"
        ).fetchall()
    return templates.TemplateResponse(
        request, "admin.html", {"user": user, "materials": materials}
    )


@app.post("/admin/materials")
def create_material(
    request: Request, title: str = Form(...), content: str = Form(...)
):
    if not require_admin(request):
        return RedirectResponse("/login", status_code=302)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO materials (title, content) VALUES (?, ?)",
            (title.strip(), content.strip()),
        )
        conn.commit()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/materials/{material_id}/delete")
def delete_material(request: Request, material_id: int):
    if not require_admin(request):
        return RedirectResponse("/login", status_code=302)
    with get_conn() as conn:
        conn.execute("DELETE FROM materials WHERE id=?", (material_id,))
        conn.commit()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/materials/{material_id}/update")
def update_material(
    request: Request,
    material_id: int,
    title: str = Form(...),
    content: str = Form(...),
):
    if not require_admin(request):
        return RedirectResponse("/login", status_code=302)
    with get_conn() as conn:
        conn.execute(
            "UPDATE materials SET title=?, content=? WHERE id=?",
            (title.strip(), content.strip(), material_id),
        )
        conn.commit()
    return RedirectResponse("/admin", status_code=302)


# -------- Student --------
@app.get("/student", response_class=HTMLResponse)
def student_home(request: Request):
    user = require_student(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    with get_conn() as conn:
        materials = conn.execute(
            "SELECT id, title, content FROM materials ORDER BY created_at DESC"
        ).fetchall()
    return templates.TemplateResponse(
        request, "student_home.html", {"user": user, "materials": materials}
    )


@app.get("/student/chat", response_class=HTMLResponse)
def student_chat_page(request: Request):
    user = require_student(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        request, "student_chat.html", {"user": user}
    )


@app.get("/student/adapt", response_class=HTMLResponse)
def student_adapt_page(request: Request):
    user = require_student(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    with get_conn() as conn:
        materials = conn.execute(
            "SELECT id, title FROM materials ORDER BY created_at DESC"
        ).fetchall()
    return templates.TemplateResponse(
        request, "student_adapt.html", {"user": user, "materials": materials}
    )


class ChatBody(BaseModel):
    message: str
    history: list = []


@app.post("/api/chat")
async def api_chat(request: Request, body: ChatBody):
    if not require_student(request):
        raise HTTPException(status_code=401, detail="Рұқсат жоқ")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT title, content FROM materials ORDER BY created_at"
        ).fetchall()
    context_text = "\n\n".join([f"### {r['title']}\n{r['content']}" for r in rows])
    try:
        reply = await chat_with_context(body.message, body.history, context_text)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"reply": reply}


class AdaptBody(BaseModel):
    material_id: int
    theme: str


@app.post("/api/adapt")
async def api_adapt(request: Request, body: AdaptBody):
    if not require_student(request):
        raise HTTPException(status_code=401, detail="Рұқсат жоқ")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT title, content FROM materials WHERE id=?", (body.material_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Материал табылмады")
    try:
        result = await adapt_content(row["title"], row["content"], body.theme)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"result": result}
