import os
import json
import re
import pdfplumber
import docx
from flask import Flask, request, send_file
from google import genai
from playwright.sync_api import sync_playwright
import os

def html_to_pdf(html_path, pdf_path):
    html_path = os.path.abspath(html_path)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )

        page = browser.new_page()

        # Load your HTML file
        page.goto(f"file:///{html_path}", wait_until="networkidle")

        # Generate PDF
        page.pdf(
            path=pdf_path,
            format="A4",
            print_background=True,
            margin={
                "top": "6mm",
                "bottom": "0mm",
                "left": "0mm",
                "right": "0mm"
            }
        )

        browser.close()

# ── CONFIG ─────────────────────────────────────────────

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "output"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
MODEL = "gemini-3-flash-preview"


# ── TEXT EXTRACTION ────────────────────────────────────

def extract_text(file_path):
    if file_path.endswith(".pdf"):
        with pdfplumber.open(file_path) as pdf:
            return "\n".join([p.extract_text() or "" for p in pdf.pages])

    elif file_path.endswith(".docx"):
        doc = docx.Document(file_path)
        return "\n".join([p.text for p in doc.paragraphs])

    return ""


# ── JSON EXTRACTION ────────────────────────────────────

def extract_json(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text


# ── NORMALIZATION FIX (CRITICAL) ───────────────────────

def normalize_data(data):

    # Fix career
    fixed_career = []
    for item in data.get("career", []):
        if isinstance(item, dict):
            fixed_career.append(item)
        else:
            fixed_career.append({
                "company": item,
                "role": "",
                "duration": ""
            })
    data["career"] = fixed_career

    # Fix education
    fixed_edu = []
    for item in data.get("education", []):
        if isinstance(item, dict):
            fixed_edu.append(item)
        else:
            fixed_edu.append({
            "degree": item,
            "institution": "",
            "location": "",
            "duration": ""
        })
    data["education"] = fixed_edu

    return data


# ── GEMINI PARSER ──────────────────────────────────────

def parse_resume(text):
    prompt = f"""
    Convert the resume into STRICT JSON format.

    Return ONLY valid JSON.

    STRUCTURE:
    {{
    "name": "",
    "title": "",
    "company_name": "",
    "role": "",
    "duration": "",
    "company_description": "",
    "summary": [],
    "skills": {{
        "category_name": ["skill1", "skill2"]
    }},
    "certifications": [],
    "responsibilities": [],
    "career": [],
    "education": [
    {{
      "degree": "",
      "institution": "",
      "location": "",
      "duration": ""
    }}
  ]
}}

    RULES:
    - "skills" must be dynamic
    - Create categories based on resume content
    - DO NOT force predefined categories
    - Only include categories that exist in the resume
    - Group similar skills under meaningful category names
    - Example categories: "Programming Languages", "Frameworks", "Tools", etc.

    RULES FOR EDUCATION:
    - Extract degree/course name
    - Extract institution/college/university
    - Extract location if available
    - Extract duration or year (e.g., 2021-2025 or 07/2021 - 07/2022)
    - ALWAYS return duration even if approximate
    - DO NOT skip duration if present anywhere in resume
    - DO NOT merge fields into one string

    Resume:
    {text}
    """
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt
    )

    raw = response.text or ""
    print("\n🔍 RAW GEMINI RESPONSE:\n", raw)

    cleaned = raw.replace("```json", "").replace("```", "").strip()
    cleaned = extract_json(cleaned)

    try:
        parsed = json.loads(cleaned)

        if not isinstance(parsed, dict):
            raise ValueError("Invalid JSON structure")

        print("\n✅ JSON PARSED SUCCESSFULLY\n")
        return parsed

    except Exception:
        print("\n❌ JSON PARSE FAILED:\n", cleaned)
        return {}


# ── BUILDERS ───────────────────────────────────────────

def build_list(items):
    return "\n".join([f"<li>{i}</li>" for i in items])


def build_skills(skills_dict):
    html = ""
    for category, skills in skills_dict.items():
        html += f'''
        <div class="skills-row">
          <div class="skills-label">{category}</div>
          <div class="skills-tags">
            {''.join([f'<span class="tag">{s}</span>' for s in skills])}
          </div>
        </div>
        '''
    return html


def build_certifications(items):
    return "".join([f'<div class="cert-box">{c}</div>' for c in items])


def build_career(items):
    html = ""
    for c in items:
        if isinstance(c, dict):
            html += f'''
            <div class="career-row">
              <span>{c.get("company","")} - {c.get("role","")}</span>
              <span>{c.get("duration","")}</span>
            </div>
            '''
    return html


def build_education(items):
    html = ""
    for e in items:
        if isinstance(e, dict):
            degree = e.get("degree", "").strip()
            institution = e.get("institution", "").strip()
            location = e.get("location", "").strip()
            duration = e.get("duration", "").strip()

            line = ""

            if degree:
                line += degree

            if institution:
                line += f" - {institution}" if line else institution

            if location:
                line += f", {location}"

            if duration:
                line += f" ({duration})"

            html += f'''
            <div class="edu-entry">
              {line}
            </div>
            '''
    return html


# ── TEMPLATE INJECTION ─────────────────────────────────

def generate_resume(data):
    with open("template.html", "r", encoding="utf-8") as f:
        html = f.read()

    html = html.replace("{{name}}", data.get("name", ""))
    html = html.replace("{{title}}", data.get("title", ""))
    html = html.replace("{{company_name}}", data.get("company_name", ""))
    html = html.replace("{{role}}", data.get("role", ""))
    html = html.replace("{{duration}}", data.get("duration", ""))
    html = html.replace("{{company_description}}", data.get("company_description", ""))

    html = html.replace("{{summary_points}}", build_list(data.get("summary", [])))
    html = html.replace("{{skills_section}}", build_skills(data.get("skills", {})))
    html = html.replace("{{certifications}}", build_certifications(data.get("certifications", [])))
    html = html.replace("{{responsibilities}}", build_list(data.get("responsibilities", [])))
    html = html.replace("{{career_synopsis}}", build_career(data.get("career", [])))
    html = html.replace("{{education}}", build_education(data.get("education", [])))

    #output_path = os.path.join(OUTPUT_FOLDER, "output.html") #for hosting
    output_path = os.path.join(OUTPUT_FOLDER, f"output_{os.getpid()}.html")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path


# ── ROUTES ─────────────────────────────────────────────

@app.route("/")
def home():
    return '''
    <h2>Upload Resume</h2>
    <form method="POST" action="/upload" enctype="multipart/form-data">
        <input type="file" name="resume" required>
        <button type="submit">Generate Resume</button>
    </form>
    '''


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("resume")

    if not file:
        return "No file uploaded"

    file_path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(file_path)

    try:
        text = extract_text(file_path)

        parsed_data = parse_resume(text)
        parsed_data = normalize_data(parsed_data)

        if not parsed_data:
            return "Failed to parse resume. Check terminal logs."

        output_file = generate_resume(parsed_data)

        pdf_file = output_file.replace(".html", ".pdf")
        html_to_pdf(output_file, pdf_file)
        return send_file(pdf_file, as_attachment=True)

    except Exception as e:
        return f"Error: {str(e)}"


# ── RUN ───────────────────────────────────────────────

if __name__ == "__main__":
    #app.run(debug=True) #for hosting
    app.run(host="0.0.0.0", port=10000)