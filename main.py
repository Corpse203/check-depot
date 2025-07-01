from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.status import HTTP_401_UNAUTHORIZED
from datetime import datetime, timedelta
import databases
import sqlalchemy
import os
import csv
import json

DATABASE_URL = os.getenv("DATABASE_URL")
database = databases.Database(DATABASE_URL)
metadata = sqlalchemy.MetaData()

submissions = sqlalchemy.Table(
    "submissions",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column("email", sqlalchemy.String),
    sqlalchemy.Column("pseudo", sqlalchemy.String),
    sqlalchemy.Column("casino", sqlalchemy.String),
    sqlalchemy.Column("ip_address", sqlalchemy.String),
    sqlalchemy.Column("submitted_at", sqlalchemy.DateTime, default=datetime.utcnow),
)

engine = sqlalchemy.create_engine(DATABASE_URL)
metadata.create_all(engine)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

security = HTTPBasic()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")


@app.on_event("startup")
async def startup():
    await database.connect()

@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()


@app.get("/", response_class=HTMLResponse)
async def form_get(request: Request):
    return templates.TemplateResponse("form.html", {"request": request})


@app.post("/submit")
async def handle_submit(request: Request, email: str = Form(...), pseudo: str = Form(...), casino: str = Form(...)):
    client_ip = request.client.host
    query = submissions.insert().values(
        email=email,
        pseudo=pseudo,
        casino=casino,
        ip_address=client_ip,
        submitted_at=datetime.utcnow()
    )
    await database.execute(query)
    return RedirectResponse(url="/", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request, credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    # Query params
    ip = request.query_params.get("ip", "")
    pseudo = request.query_params.get("pseudo", "")
    casino = request.query_params.get("casino", "")
    date_range = request.query_params.get("range", "")
    page = int(request.query_params.get("page", 1))
    per_page = 10
    offset = (page - 1) * per_page

    filters = []
    values = {}

    if ip:
        filters.append("ip_address ILIKE :ip")
        values["ip"] = f"%{ip}%"
    if pseudo:
        filters.append("pseudo ILIKE :pseudo")
        values["pseudo"] = f"%{pseudo}%"
    if casino:
        filters.append("casino ILIKE :casino")
        values["casino"] = f"%{casino}%"
    if date_range in ["7", "30"]:
        days = int(date_range)
        filters.append("submitted_at >= :since")
        values["since"] = datetime.utcnow() - timedelta(days=days)

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

    query = f"""
        SELECT ip_address, array_agg(json_build_object(
            'email', email,
            'pseudo', pseudo,
            'casino', casino,
            'submitted_at', submitted_at
        )) AS entries
        FROM submissions
        {where_clause}
        GROUP BY ip_address
        ORDER BY MAX(submitted_at) DESC
        LIMIT :limit OFFSET :offset
    """
    values["limit"] = per_page + 1
    values["offset"] = offset
    rows = await database.fetch_all(query=query, values=values)

    has_next = len(rows) > per_page

    # 🔄 Traiter les résultats pour affichage
    processed_rows = []
    for r in rows[:per_page]:
        raw_entries = r["entries"]
        parsed_entries = [dict(e) for e in raw_entries]
        processed_rows.append({
            "ip_address": r["ip_address"],
            "entries": parsed_entries
        })

    # 🔄 Correction ici : utiliser fetch_val au lieu de execute
    count_values = {k: v for k, v in values.items() if k not in ["limit", "offset"]}
    total_query = f"SELECT COUNT(DISTINCT ip_address) FROM submissions {where_clause}"
    total_count = await database.fetch_val(query=total_query, values=count_values)

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "entries_by_ip": processed_rows,
        "total_count": total_count,
        "ip": ip,
        "pseudo": pseudo,
        "casino": casino,
        "range": date_range,
        "page": page,
        "has_next": has_next
    })


@app.get("/export")
async def export_csv(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    
    query = "SELECT * FROM submissions ORDER BY submitted_at DESC"
    rows = await database.fetch_all(query)

    csv_path = "static/submissions_export.csv"
    with open(csv_path, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["id", "email", "pseudo", "casino", "ip_address", "submitted_at"])
        for r in rows:
            writer.writerow([r["id"], r["email"], r["pseudo"], r["casino"], r["ip_address"], r["submitted_at"]])
    
    return FileResponse(csv_path, media_type='text/csv', filename="submissions_export.csv")
