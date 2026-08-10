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
                "top": "0mm",
                "bottom": "6mm",
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
                "duration": "",
                "company_description": "",
                "responsibilities": []
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
    "summary": [],
    "skills": {{
        "category_name": ["skill1", "skill2"]
    }},
    "certifications": [],
    "internships": [],

    "career": [
    {{
        "company": "",
        "role": "",
        "duration": "",
        "company_description": "",
        "responsibilities": []
    }}
    ],
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
    - "company_description" = a brief description of the COMPANY/ORGANIZATION itself, based only on information available in the resume.
    - Only include categories that exist in the resume
    - Group similar skills under meaningful category names
    - Example categories: "Programming Languages", "Frameworks", "Tools", etc.

    RULES FOR EDUCATION:
    - Extract degree/course name
    - Extract institution/college/university
    - Extract location if available
    - Extract duration or passedout year (e.g., April 2025 or Mar 2025)
    - ALWAYS return duration even if approximate
    - DO NOT skip duration if present anywhere in resume
    - DO NOT merge fields into one string

    RULES FOR INTERNSHIPS:
    - Extract internships separately from regular work experience.
    - Include an internship only if the resume explicitly mentions an internship, intern, internship experience, summer internship, research internship, or similar.
    - Do NOT classify regular employment as an internship.
    - Return each internship as one concise string.
    - Include the organization/company name, internship role/title, and duration if available.
    - If there are no internships, return an empty array.

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

def build_certifications_section(items):
    if not items:
        return ""

    certs = "".join(
        f'<div class="cert-box">{c}</div>'
        for c in items
    )

    return f"""
    <div class="section-header">
        <div class="section-title">CERTIFICATIONS</div>
        <div class="section-line"></div>
    </div>

    {certs}
    """

def build_internships_section(items):
    if not items:
        return ""

    internships = "".join(
        f'<div class="cert-box">{i}</div>'
        for i in items
    )

    return f"""
    <div class="section-header">
        <div class="section-title">INTERNSHIP EXPERIENCE</div>
        <div class="section-line"></div>
    </div>

    {internships}
    """

def build_work_experience(items):

    html = ""

    for job in items:

        company = job.get("company", "")
        role = job.get("role", "")
        duration = job.get("duration", "")
        company_description = job.get("company_description", "")
        responsibilities = job.get("responsibilities", [])

        html += f"""
        <div class="work-entry">

            <div class="company-bar">
                <span class="company-dot"></span>
                {company}
            </div>

            <div style="padding-left:4px;">

                <div class="work-meta">
                    <span>Role:</span> {role}
                </div>

                <div class="work-meta">
                    <span>Duration:</span> {duration}
                </div>

                { f"<div class='work-meta'><span>Description:</span> {company_description}</div>" if company_description else ""
}

                {
                    "<div class='work-meta' style='margin-top:6px;'><span>Roles & Responsibilities</span></div>"
                    if responsibilities else ""
                }

                <ul class="work-responsibilities">
                    {''.join(f'<li>{r}</li>' for r in responsibilities)}
                </ul>

            </div>

        </div>
        """

    return html

def build_career(items):
    html = ""

    for c in items:
        if not isinstance(c, dict):
            continue

        company = c.get("company", "")
        role = c.get("role", "")
        duration = c.get("duration", "")

        html += f"""
        <div class="career-row">

            <div class="career-company">
                {company}
            </div>

            <div class="career-role">
                {role}
            </div>

            <div class="career-duration">
                {duration}
            </div>

        </div>
        """

    return html


def build_education(items):

    html = ""

    for e in items:

        if not isinstance(e, dict):
            continue

        degree = e.get("degree", "").strip()
        institution = e.get("institution", "").strip()
        location = e.get("location", "").strip()
        duration = e.get("duration", "").strip()

        # Combine institution + location
        institution_text = institution

        if location:
            institution_text += f", {location}" if institution_text else location

        html += f"""
        <div class="education-row">

            <div class="education-degree">
                {degree}
            </div>

            <div class="education-institution">
                {institution_text}
            </div>

            <div class="education-duration">
                {duration}
            </div>

        </div>
        """

    return html

# ── TEMPLATE INJECTION ─────────────────────────────────

def generate_resume(data):
    with open("template.html", "r", encoding="utf-8") as f:
        html = f.read()

    html = html.replace("{{name}}", data.get("name", ""))
    html = html.replace("{{title}}", data.get("title", ""))
    html = html.replace("{{work_experience}}", build_work_experience(data.get("career", [])))
    html = html.replace("{{summary_points}}", build_list(data.get("summary", [])))
    html = html.replace("{{skills_section}}", build_skills(data.get("skills", {})))
    html = html.replace("{{certifications_section}}", build_certifications_section(data.get("certifications", [])))
    html = html.replace("{{internships_section}}", build_internships_section(data.get("internships", [])))
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
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>CS Resume Generator</title>

        <style>

            * {
                box-sizing: border-box;
            }

            body {
                margin: 0;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                background: #f4f7fa;
                font-family: Arial, sans-serif;
                color: #26384a;
            }

            .card {
                width: 520px;
                background: white;
                padding: 40px;
                border-radius: 16px;
                box-shadow: 0 15px 45px rgba(0,0,0,0.10);
            }

            .logo {
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 2px;
                color: #3f74c7;
                margin-bottom: 8px;
            }

            h1 {
                margin: 0 0 8px;
                font-size: 28px;
                color: #26384a;
            }

            .subtitle {
                margin: 0 0 30px;
                color: #7b8794;
                font-size: 14px;
            }

            .field {
                margin-bottom: 22px;
            }

            label {
                display: block;
                font-size: 13px;
                font-weight: bold;
                margin-bottom: 8px;
                color: #34495e;
            }

            input[type="text"],
            input[type="file"] {
                width: 100%;
                padding: 12px;
                border: 1px solid #d5dde5;
                border-radius: 8px;
                font-size: 14px;
                background: #fff;
            }

            input[type="text"]:focus {
                outline: none;
                border-color: #3f74c7;
                box-shadow: 0 0 0 3px rgba(63,116,199,0.10);
            }

            input[type="file"] {
                cursor: pointer;
            }

            .generate-btn {
                width: 100%;
                padding: 14px;
                border: none;
                border-radius: 8px;
                background: #2c3e50;
                color: white;
                font-size: 15px;
                font-weight: bold;
                cursor: pointer;
                transition: 0.2s;
            }

            .generate-btn:hover {
                background: #3a5068;
            }

            /* LOADING */

            #loading {
                display: none;
                text-align: center;
            }

            .loader {
                width: 70px;
                height: 70px;
                margin: 0 auto 25px;

                border: 5px solid #e8edf3;
                border-top: 5px solid #3f74c7;
                border-right: 5px solid #3f74c7;

                border-radius: 50%;

                animation: spin 0.9s linear infinite;
            }

            @keyframes spin {
                from {
                    transform: rotate(0deg);
                }

                to {
                    transform: rotate(360deg);
                }
            }

            .loading-title {
                font-size: 20px;
                font-weight: bold;
                color: #26384a;
                margin-bottom: 10px;
            }

            .loading-status {
                font-size: 14px;
                color: #6c7a89;
                min-height: 22px;
            }

            .progress-container {
                margin-top: 25px;
                height: 6px;
                width: 100%;
                background: #e9eef3;
                border-radius: 10px;
                overflow: hidden;
            }

            .progress-bar {
                height: 100%;
                width: 10%;
                background: #3f74c7;
                border-radius: 10px;
                transition: width 0.8s ease;
            }

            .check {
                display: none;
                font-size: 50px;
                margin-bottom: 15px;
            }

            .error {
                display: none;
                margin-top: 20px;
                padding: 12px;
                border-radius: 8px;
                background: #fff1f1;
                color: #c0392b;
                font-size: 13px;
            }

        </style>
    </head>

    <body>

        <div class="card">

            <div id="form-section">

                <div class="logo">CAPESTART</div>

                <h1>Resume Generator</h1>

                <p class="subtitle">
                    Upload a resume and generate a professionally CS formatted PDF.
                </p>

                <form id="resumeForm">

                    <div class="field">
                        <label for="title">
                            Candidate Title
                        </label>

                        <input
                            type="text"
                            id="title"
                            name="title"
                            placeholder="e.g. Senior Research Analyst"
                            required
                        >
                    </div>

                    <div class="field">
                        <label for="resume">
                            Upload Resume
                        </label>

                        <input
                            type="file"
                            id="resume"
                            name="resume"
                            accept=".pdf,.docx"
                            required
                        >
                    </div>

                    <button
                        type="submit"
                        class="generate-btn"
                        id="generateBtn"
                    >
                        Generate Resume
                    </button>

                </form>

                <div id="error" class="error"></div>

            </div>


            <!-- LOADING SCREEN -->

            <div id="loading">

                <div class="loader" id="loader"></div>

                <div class="check" id="check">
                    ✓
                </div>

                <div class="loading-title" id="loadingTitle">
                    Generating Resume
                </div>

                <div class="loading-status" id="loadingStatus">
                    Preparing your resume...
                </div>

                <div class="progress-container">
                    <div
                        class="progress-bar"
                        id="progressBar"
                    ></div>
                </div>

            </div>

        </div>


        <script>

            const form = document.getElementById("resumeForm");

            const formSection = document.getElementById("form-section");

            const loading = document.getElementById("loading");

            const loadingStatus =
                document.getElementById("loadingStatus");

            const loadingTitle =
                document.getElementById("loadingTitle");

            const progressBar =
                document.getElementById("progressBar");

            const loader =
                document.getElementById("loader");

            const check =
                document.getElementById("check");

            const errorBox =
                document.getElementById("error");


            form.addEventListener("submit", async function(e) {

                e.preventDefault();

                const formData = new FormData(form);

                formSection.style.display = "none";

                loading.style.display = "block";

                errorBox.style.display = "none";


                const stages = [

                    {
                        text: "Uploading resume...",
                        progress: 10
                    },

                    {
                        text: "Extracting contents from resume...",
                        progress: 25
                    },

                    {
                        text: "Analyzing resume text...",
                        progress: 40
                    },

                    {
                        text: "Parsing resume information...",
                        progress: 55
                    },

                    {
                        text: "Structuring candidate profile...",
                        progress: 65
                    },

                    {
                        text: "Building resume layout...",
                        progress: 78
                    },

                    {
                        text: "Rendering professional PDF...",
                        progress: 90
                    },

                    {
                        text: "Preparing your download...",
                        progress: 97
                    }

                ];


                let stageIndex = 0;


                function updateStage() {

                    if (stageIndex >= stages.length) {
                        return;
                    }

                    loadingStatus.innerText =
                        stages[stageIndex].text;

                    progressBar.style.width =
                        stages[stageIndex].progress + "%";

                    stageIndex++;

                }


                updateStage();


                const stageTimer = setInterval(() => {

                    updateStage();

                }, 1500);


                try {

                    const response = await fetch(
                        "/upload",
                        {
                            method: "POST",
                            body: formData
                        }
                    );


                    clearInterval(stageTimer);


                    if (!response.ok) {

                        const errorText =
                            await response.text();

                        throw new Error(errorText);

                    }


                    loadingStatus.innerText =
                        "Resume generated successfully!";

                    progressBar.style.width = "100%";


                    const blob =
                        await response.blob();


                    const url =
                        window.URL.createObjectURL(blob);


                    const a =
                        document.createElement("a");

                    a.href = url;

                    a.download = "resume.pdf";

                    document.body.appendChild(a);

                    a.click();

                    a.remove();

                    window.URL.revokeObjectURL(url);


                    loader.style.display = "none";

                    check.style.display = "block";

                    loadingTitle.innerText =
                        "Resume Ready";

                    loadingStatus.innerText =
                        "Your PDF has been downloaded.";

                }

                catch(error) {

                    clearInterval(stageTimer);

                    loading.style.display = "none";

                    formSection.style.display = "block";

                    errorBox.style.display = "block";

                    errorBox.innerText =
                        "Something went wrong while generating the resume.";

                    console.error(error);

                }

            });

        </script>

    </body>
    </html>
    """


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("resume")

    title = request.form.get("title", "").strip()

    if not file:
        return "No file uploaded", 400
    
    if not title:
        return "Candidate title is required", 400

    filename  = file.filename or "resume"
    file_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(file_path)

    try:
        print(f"\n📁 File saved: {file_path}")

        print("📄 Extracting text...")
        text = extract_text(file_path)
        print(f"✅ Text extracted: {len(text)} chars")

        print("🤖 Calling Gemini...")
        parsed_data = parse_resume(text)
        print(f"✅ Gemini done: {list(parsed_data.keys()) if parsed_data else 'EMPTY'}")

        parsed_data["title"] = title
        parsed_data = normalize_data(parsed_data)

        if not parsed_data:
            return "Failed to parse resume — Gemini returned empty. Check terminal.", 500

        print("🖊️  Generating HTML...")
        output_html = generate_resume(parsed_data)
        print(f"✅ HTML written: {output_html}")

        output_pdf = output_html.replace(".html", ".pdf")

        print("🖨️  Rendering PDF with Playwright...")
        html_to_pdf(output_html, output_pdf)
        print(f"✅ PDF written: {output_pdf}")

        return send_file(output_pdf, as_attachment=True, download_name="resume.pdf")

    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"\n❌ UPLOAD ERROR:\n{error_detail}")
        # Return full traceback in browser so you can see exactly what failed
        return f"<pre>ERROR:\n{error_detail}</pre>", 500

# ── RUN ───────────────────────────────────────────────

if __name__ == "__main__":
    #app.run(debug=True) #for hosting
    app.run(host="0.0.0.0", port=10000)