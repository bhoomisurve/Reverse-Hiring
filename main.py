import os
from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
import google.generativeai as genai
from datetime import datetime
import requests
import json
from bson import ObjectId

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'  # Replace with a secure secret key

# Initialize login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

genai.configure(api_key="{cant share on github}")

# Initialize MongoDB connection
mongo_client = MongoClient("mongodb+srv://survebhoomika:<password>@cluster0.vc0e9.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
db = mongo_client["reverse_hiring"]
users_collection = db["users"]
companies_collection = db["companies"]
applications_collection = db["applications"]
skill_tests_collection = db["skill_tests"]

# User class for Flask-Login
class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data["_id"])
        self.email = user_data["email"]
        self.name = user_data["name"]
        self.user_type = user_data["user_type"]  # 'candidate' or 'company'
        self.data = user_data

    def get_id(self):
        return self.id

@login_manager.user_loader
def load_user(user_id):
    user_data = users_collection.find_one({"_id": ObjectId(user_id)})
    if user_data:
        return User(user_data)
    return None

# Helper function to convert ObjectId to string for JSON serialization
class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        return json.JSONEncoder.default(self, o)

# Routes
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        user_data = request.form.to_dict()
        user_data['password'] = generate_password_hash(user_data['password'])
        user_data['created_at'] = datetime.utcnow()
        
        # Set default values based on user type
        if user_data['user_type'] == 'candidate':
            user_data['tech_rank'] = 0
            user_data['skills'] = []
            user_data['resume_analysis'] = {}
            user_data['completed_tests'] = []
        else:  # company
            user_data['company_description'] = ''
            user_data['job_listings'] = []
        
        result = users_collection.insert_one(user_data)
        user_data['_id'] = result.inserted_id
        
        user = User(user_data)
        login_user(user)
        
        return redirect(url_for('dashboard'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user_data = users_collection.find_one({"email": email})
        
        if user_data and check_password_hash(user_data['password'], password):
            user = User(user_data)
            login_user(user)
            return redirect(url_for('dashboard'))
        
        return render_template('login.html', error='Invalid email or password')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.user_type == 'candidate':
        return render_template('candidate_dashboard.html')
    else:
        return render_template('company_dashboard.html')

# AI Resume Analysis
@app.route('/upload_resume', methods=['POST'])
@login_required
def upload_resume():
    if current_user.user_type != 'candidate':
        return jsonify({"error": "Only candidates can upload resumes"}), 403
    
    if 'resume' not in request.files:
        return jsonify({"error": "No resume file provided"}), 400
    
    resume_file = request.files['resume']
    
    # Check if the file is empty
    if resume_file.filename == '':
        return jsonify({"error": "Empty file submitted"}), 400
    
    # Add file type checking
    filename = resume_file.filename
    file_ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    
    if file_ext == 'pdf':
        # Handle PDF (requires PyPDF2 or pdfminer)
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(resume_file)
            resume_text = ""
            for page in reader.pages:
                resume_text += page.extract_text()
        except ImportError:
            return jsonify({"error": "PDF parsing not available. Install PyPDF2"}), 500
        except Exception as e:
            return jsonify({"error": f"Failed to parse PDF: {str(e)}"}), 400
    elif file_ext in ['doc', 'docx']:
        # Handle Word documents (requires python-docx)
        try:
            import docx
            doc = docx.Document(resume_file)
            resume_text = "\n".join([p.text for p in doc.paragraphs])
        except ImportError:
            return jsonify({"error": "Word document parsing not available. Install python-docx"}), 500
        except Exception as e:
            return jsonify({"error": f"Failed to parse Word document: {str(e)}"}), 400
    elif file_ext == 'txt':
        # Plain text files
        resume_text = resume_file.read().decode('utf-8', errors='replace')
    else:
        return jsonify({"error": f"Unsupported file format: {file_ext}. Please upload PDF, DOC, DOCX, or TXT files"}), 400
    
    # Log the extracted text for debugging
    print(f"Extracted resume text ({len(resume_text)} chars):", resume_text[:200] + "...")
    
    try:
        # Analyze resume using GenAI
        analysis = analyze_resume_with_genai(resume_text)
        
        # Update user profile with resume analysis
        users_collection.update_one(
            {"_id": ObjectId(current_user.id)},
            {"$set": {"resume_analysis": analysis, "last_updated": datetime.utcnow()}}
        )
        
        return jsonify({"success": True, "analysis": analysis})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Resume analysis failed: {str(e)}"}), 500
    
def analyze_resume_with_genai(resume_text):
    prompt = f"""
    Analyze the following resume and extract:
    1. Skills (technical and soft skills)
    2. Experience (years and quality)
    3. Education
    4. Project highlights
    5. Strengths and weaknesses
    
    Resume:
    {resume_text}
    
    Format your response as JSON with these keys: skills, experience, education, projects, strengths, weaknesses
    """
    
    # Update the model name to gemini-2.0-flash
    model = genai.GenerativeModel('gemini-2.0-flash')
    response = model.generate_content(prompt)
    
    try:
        # Add proper debugging here
        print("GenAI raw response:", response.text)
        analysis = json.loads(response.text)
        return analysis
    except json.JSONDecodeError as e:
        print(f"JSON parsing error: {e}")
        # Improved error handling with structured response
        # Try to salvage information from non-JSON response
        try:
            # Extract skills section
            skills = []
            if "skills" in response.text.lower():
                skills_section = response.text.lower().split("skills")[1].split("\n")[0:5]
                skills = [s.strip("- :,").strip() for s in skills_section if s.strip()]
            
            # Extract experience section
            experience = ""
            if "experience" in response.text.lower():
                experience_section = response.text.lower().split("experience")[1].split("\n")[0:5]
                experience = " ".join([s.strip("- :,").strip() for s in experience_section if s.strip()])
            
            # Extract education section
            education = ""
            if "education" in response.text.lower():
                education_section = response.text.lower().split("education")[1].split("\n")[0:5]
                education = " ".join([s.strip("- :,").strip() for s in education_section if s.strip()])
            
            # Extract projects section
            projects = []
            if "projects" in response.text.lower():
                projects_section = response.text.lower().split("projects")[1].split("\n")[0:5]
                projects = [s.strip("- :,").strip() for s in projects_section if s.strip()]
            
            # Extract strengths section
            strengths = []
            if "strengths" in response.text.lower():
                strengths_section = response.text.lower().split("strengths")[1].split("\n")[0:5]
                strengths = [s.strip("- :,").strip() for s in strengths_section if s.strip()]
            
            # Extract weaknesses section
            weaknesses = []
            if "weaknesses" in response.text.lower():
                weaknesses_section = response.text.lower().split("weaknesses")[1].split("\n")[0:5]
                weaknesses = [s.strip("- :,").strip() for s in weaknesses_section if s.strip()]
            
            # Return structured response
            return {
                "skills": skills,
                "experience": experience,
                "education": education,
                "projects": projects,
                "strengths": strengths,
                "weaknesses": weaknesses,
                "raw_response": response.text
            }
        except Exception as parsing_error:
            print(f"Secondary parsing error: {parsing_error}")
            return {
                "skills": [],
                "experience": "",
                "education": "",
                "projects": [],
                "strengths": [],
                "weaknesses": [],
                "error": "Could not parse AI response",
                "raw_response": response.text
            }
# Skill Tests
@app.route('/available_tests')
@login_required
def available_tests():
    if current_user.user_type != 'candidate':
        return jsonify({"error": "Only candidates can take skill tests"}), 403
    
    tests = list(skill_tests_collection.find())
    return jsonify(JSONEncoder().encode(tests))

@app.route('/take_test/<test_id>', methods=['GET', 'POST'])
@login_required
def take_test(test_id):
    if current_user.user_type != 'candidate':
        return jsonify({"error": "Only candidates can take skill tests"}), 403
    
    test = skill_tests_collection.find_one({"_id": ObjectId(test_id)})
    
    if not test:
        return jsonify({"error": "Test not found"}), 404
    
    if request.method == 'GET':
        # Return the test questions
        return jsonify(JSONEncoder().encode(test))
    
    if request.method == 'POST':
        # Process the test submission
        answers = request.json.get('answers', {})
        
        # Evaluate the test (simplified example)
        score = evaluate_test(test, answers)
        
        # Update user's completed tests and recalculate TechRank
        completed_test = {
            "test_id": test_id,
            "test_name": test["name"],
            "score": score,
            "completed_at": datetime.utcnow()
        }
        
        users_collection.update_one(
            {"_id": ObjectId(current_user.id)},
            {
                "$push": {"completed_tests": completed_test},
                "$set": {"last_updated": datetime.utcnow()}
            }
        )
        
        # Recalculate TechRank
        update_tech_rank(current_user.id)
        
        return jsonify({"success": True, "score": score})
    
def evaluate_test(test, answers):
    # Simplified test evaluation logic
    # In a real implementation, this would be more sophisticated and support different question types
    
    correct_answers = 0
    total_questions = len(test["questions"])
    
    for q_id, question in enumerate(test["questions"]):
        if str(q_id) in answers and answers[str(q_id)] == question["correct_answer"]:
            correct_answers += 1
    
    score = (correct_answers / total_questions) * 100
    return score

def update_tech_rank(user_id):
    user = users_collection.find_one({"_id": ObjectId(user_id)})
    
    if not user or "completed_tests" not in user:
        return
    
    # Simple TechRank calculation based on test scores and resume analysis
    # In a real implementation, this would be more sophisticated
    
    # Calculate average test score
    test_scores = [test["score"] for test in user["completed_tests"]]
    avg_test_score = sum(test_scores) / len(test_scores) if test_scores else 0
    
    # Factor in resume quality (simplified)
    resume_score = 0
    if "resume_analysis" in user and user["resume_analysis"]:
        resume_score = len(user["resume_analysis"].get("skills", [])) * 2
        
        # Add points for years of experience
        experience = user["resume_analysis"].get("experience", "")
        if "years" in experience.lower():
            try:
                years = int(''.join(filter(str.isdigit, experience)))
                resume_score += min(years * 5, 25)  # Cap at 25 points
            except:
                pass
    
    # Calculate TechRank (max 100)
    tech_rank = min(int((avg_test_score * 0.7) + (resume_score * 0.3)), 100)
    
    # Update user's TechRank
    users_collection.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"tech_rank": tech_rank}}
    )

# Company routes
@app.route('/top_candidates')
@login_required
def top_candidates():
    if current_user.user_type != 'company':
        return jsonify({"error": "Only companies can view top candidates"}), 403
    
    # Get candidates with TechRank, sorted by rank
    candidates = list(users_collection.find(
        {"user_type": "candidate", "tech_rank": {"$gt": 0}},
        {"name": 1, "tech_rank": 1, "skills": 1, "resume_analysis.skills": 1}
    ).sort("tech_rank", -1).limit(50))
    
    return jsonify(JSONEncoder().encode(candidates))

@app.route('/apply_to_candidate', methods=['POST'])
@login_required
def apply_to_candidate():
    if current_user.user_type != 'company':
        return jsonify({"error": "Only companies can apply to candidates"}), 403
    
    data = request.json
    candidate_id = data.get('candidate_id')
    job_details = data.get('job_details', {})
    
    # Create application record
    application = {
        "company_id": ObjectId(current_user.id),
        "company_name": current_user.name,
        "candidate_id": ObjectId(candidate_id),
        "job_title": job_details.get("title", ""),
        "salary": job_details.get("salary", ""),
        "benefits": job_details.get("benefits", []),
        "work_arrangement": job_details.get("work_arrangement", ""),
        "message": job_details.get("message", ""),
        "status": "pending",
        "created_at": datetime.utcnow()
    }
    
    result = applications_collection.insert_one(application)
    
    return jsonify({"success": True, "application_id": str(result.inserted_id)})

@app.route('/my_applications')
@login_required
def my_applications():
    if current_user.user_type == 'candidate':
        # Get applications received by the candidate
        applications = list(applications_collection.find(
            {"candidate_id": ObjectId(current_user.id)}
        ).sort("created_at", -1))
    else:
        # Get applications sent by the company
        applications = list(applications_collection.find(
            {"company_id": ObjectId(current_user.id)}
        ).sort("created_at", -1))
    
    return jsonify(JSONEncoder().encode(applications))

@app.route('/respond_to_application/<application_id>', methods=['POST'])
@login_required
def respond_to_application(application_id):
    if current_user.user_type != 'candidate':
        return jsonify({"error": "Only candidates can respond to applications"}), 403
    
    data = request.json
    status = data.get('status')  # 'accepted', 'rejected', 'negotiating'
    message = data.get('message', '')
    
    applications_collection.update_one(
        {"_id": ObjectId(application_id), "candidate_id": ObjectId(current_user.id)},
        {
            "$set": {
                "status": status,
                "candidate_response": message,
                "responded_at": datetime.utcnow()
            }
        }
    )
    
    return jsonify({"success": True})

# Admin routes for creating skill tests
@app.route('/create_skill_test', methods=['POST'])
@login_required
def create_skill_test():
    # In a real app, you'd check for admin privileges
    test_data = request.json
    
    test = {
        "name": test_data.get("name"),
        "category": test_data.get("category"),
        "description": test_data.get("description"),
        "questions": test_data.get("questions", []),
        "created_at": datetime.utcnow()
    }
    
    result = skill_tests_collection.insert_one(test)
    
    return jsonify({"success": True, "test_id": str(result.inserted_id)})

# API endpoints for code testing integration
@app.route('/api/code_test', methods=['POST'])
@login_required
def code_test():
    if current_user.user_type != 'candidate':
        return jsonify({"error": "Only candidates can take code tests"}), 403
    
    data = request.json
    code = data.get('code', '')
    language = data.get('language', 'python')
    test_cases = data.get('test_cases', [])
    
    # In a real implementation, you would use a code execution API
    # This is a simplified mock
    results = mock_code_execution(code, language, test_cases)
    
    return jsonify(results)

def mock_code_execution(code, language, test_cases):
    # Mock function that would be replaced with a real code execution API
    # In a real implementation, you'd use something like Judge0, HackerRank API, etc.
    
    passed_cases = []
    failed_cases = []
    
    for i, case in enumerate(test_cases):
        # Simulate some test failures
        if i % 3 == 0:
            failed_cases.append({
                "input": case["input"],
                "expected": case["expected"],
                "actual": "mock output",
                "error": "Mock error message"
            })
        else:
            passed_cases.append({
                "input": case["input"],
                "expected": case["expected"],
                "actual": case["expected"]  # In mock, we pretend output matches expected
            })
    
    return {
        "success": len(failed_cases) == 0,
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "total_cases": len(test_cases),
        "passed_count": len(passed_cases),
        "execution_time": 0.123  # Mock execution time
    }

if __name__ == '__main__':
    app.run(debug=True)
