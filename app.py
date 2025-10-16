# app.py

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, make_response, json
import psycopg2
from psycopg2 import extras

from werkzeug.security import generate_password_hash, check_password_hash
import uuid
from datetime import datetime, timedelta
import json
from collections import Counter
from functools import wraps
from flask import Blueprint, send_from_directory
import os
import requests
import time
from flask import render_template_string, send_from_directory
import secrets


# This is a sample host for an external service. In a real application, this should be in a config file.
DEFAULT_HOST = "https://dcm4chee.org/dcm4chee-arc/aets/DCM4CHEE/rs"
SHARED_API_KEY = "hospital_shared_key"
# To avoid an insecure request warning, we'll use a local mock for the example.
# A full implementation would use a proper secure endpoint.
# The endpoint is not used in this app as the focus is on UI and database.


# --- Application Setup ---
app = Flask(__name__)
app.secret_key = 'your_super_secret_key' # IMPORTANT: Change this in production!
# Configure session
app.secret_key = secrets.token_hex(16)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = True
# Database Configuration
DB_HOST = "localhost"
DB_NAME = "postgres"
DB_USER = "postgres"
DB_PASS = "Radha@99"

# Create a 'downloads' directory to store the test reports
os.makedirs("downloads", exist_ok=True)
HISTORY_PATH = os.path.join("downloads", "order_history.json")

# --- History Helpers ---
def load_history(department=None):
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        import json as _json
        with open(HISTORY_PATH, 'r', encoding='utf-8') as f:
            history = _json.load(f) or []
            
            # Filter by department if specified
            if department:
                history = [order for order in history if order.get('department') == department]
                
            return history
    except Exception:
        return []

def save_history(history):
    try:
        import json as _json
        with open(HISTORY_PATH, 'w', encoding='utf-8') as f:
            _json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def record_order(entry):
    history = load_history()
    history.insert(0, entry)
    save_history(history)


def get_db_connection():
    """Establishes and returns a connection to the PostgreSQL database."""
    conn = None
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        return conn
    except psycopg2.Error as e:
        print(f"Database connection error: {e}")
        flash("Database connection error. Please try again later.", "error")
        return None
    
# --- Test Categories from Laboratory Management System ---
TEST_CATEGORIES = {
    'biochemistry': {
        'Kidney Function': ['GLU', 'UREA', 'CREATININE'],
        'Liver Function': ['SGOT', 'SGPT', 'ALBUMIN', 'TOTAL_BILIRUBIN'],
        'Thyroid Function': ['TSH', 'T3', 'T4'],
        'Cardiac Markers': ['TROPONIN_I'],
        'Lipid Profile': ['TOTAL_CHOLESTEROL', 'HDL', 'LDL'],
        'Electrolytes': ['SODIUM', 'POTASSIUM']
    },
    'microbiology': {
        'Wet Mount & Staining': ['GRAM_STAIN', 'HANGING_DROP', 'INDIA_INK', 'STOOL_OVA', 'KOH_MOUNT', 'ZN_STAIN'],
        'Culture & Sensitivity': ['BLOOD_CULTURE', 'URINE_CULTURE', 'SPUTUM_CULTURE', 'WOUND_CULTURE', 'THROAT_CULTURE', 'CSF_CULTURE'],
        'Fungal Culture': ['FUNGAL_CULTURE', 'FUNGAL_ID', 'ANTIFUNGAL_SENS'],
        'Serology': ['WIDAL', 'TYPHIDOT', 'DENGUE_NS1', 'MALARIA_AG', 'HIV_ELISA', 'HBSAG']
    },
    'pathology': {
        'Histopathology': ['BIOPSY_HISTOPATHOLOGY', 'SURGICAL_PATHOLOGY'],
        'Hematology': ['CBC', 'PERIPHERAL_SMEAR', 'BONE_MARROW', 'COAGULATION'],
        'Immunohistochemistry': ['IHC_MARKERS', 'SPECIAL_STAINS', 'MOLECULAR_PATH']
    }
}

# --- Helpers ---

def save_test_stream_to_file(resp, out_path, chunk_size=8192):
    """Saves a streaming response content to a file."""
    with open(out_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size):
            if chunk:
                f.write(chunk)

def download_report(host, order_id, out_dir, uhid=None):
    """Downloads a test report by order ID."""
    url = f"{host.rstrip('/')}/api/orders/{order_id}"
    try:
        with requests.get(url, stream=True, timeout=30, headers={'X-API-Key': SHARED_API_KEY}) as r:
            if r.ok:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                fname = f"{uhid or 'patient'}_{order_id}_{ts}.json"
                
                out_path = os.path.join(out_dir, fname)
                save_test_stream_to_file(r, out_path)
                return fname
            else:
                return None
    except Exception:
        return None

def poll_test_request_status(host, order_id, timeout_s, poll_interval_s, out_dir, uhid):
    """Polls the status of a request until it's completed or times out."""
    status_url = f"{host.rstrip('/')}/api/orders/{order_id}"
    started = time.time()
    while time.time() - started < timeout_s:
        try:
            r = requests.get(status_url, timeout=15, headers={'X-API-Key': SHARED_API_KEY})
            if r.ok:
                j = r.json()
                # Check if any department has completed results
                per_dept = j.get('perDepartment', [])
                for dept in per_dept:
                    if dept.get('status') == 'completed' and dept.get('results'):
                        return download_report(host, order_id, out_dir, uhid)
        except requests.RequestException:
            # Ignore connection errors and continue polling
            pass
        time.sleep(poll_interval_s)
    return None

def perform_test_request(host, department, uhid, tests, priority='routine', specimen='Blood', clinical_notes=''):
    """Performs the API request to create a lab test order."""
    url = f"{host.rstrip('/')}/api/orders"
    payload = {
        "externalOrderId": f"EXT_{uhid}_{int(time.time())}",
        "priority": priority,
        "patient": {
            "uhid": uhid,
            "name": f"Patient {uhid}",
            "age": 30,
            "gender": "Not Specified"
        },
        "clinician": {
            "name": f"Dr. {department.title()}",
            "department": department,
            "contact": "Not Specified"
        },
        "tests": [{"testCode": test} for test in tests],
        "panels": [],
        "specimen": specimen,
        "clinicalNotes": clinical_notes
    }
    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': SHARED_API_KEY
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
    except requests.RequestException as e:
        return None, f"Request error: {e}"

    if resp.status_code == 201:
        j = resp.json()
        order_id = j.get('orderId')
        if order_id:
            # Record to local history
            record_order({
                'orderId': order_id,
                'externalOrderId': payload.get('externalOrderId'),
                'uhid': uhid,
                'department': department,
                'priority': priority,
                'tests': tests,
                'specimen': specimen,
                'createdAt': datetime.now().isoformat()
            })
            # Return the actual order ID from the main system
            return order_id, None
        else:
            return None, "No order ID received from server"
    
    return None, f"Server returned error {resp.status_code}: {resp.text[:400]}"    

# API Key Management (In-memory for this example, use a database in production)
API_KEYS = {
    "optho-7589-abcde-01": "Ophthalmology Department",
    "admin-4567-fghij-02": "Administration Department"
}

def validate_api_key(f):
    """Decorator to validate the API key in the request header."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get("X-API-Key")
        if not api_key or api_key not in API_KEYS:
            return jsonify({"error": "Unauthorized. Invalid or missing API key."}), 401
        
        # You can also pass the department name to the function if needed
        # request.api_user = API_KEYS[api_key]
        return f(*args, **kwargs)
    return decorated_function

# Create a 'downloads' directory to store the DICOM files
os.makedirs("downloads", exist_ok=True)

# --- Helpers ---

def save_stream_to_file(resp, out_path, chunk_size=8192):
    """Saves a streaming response content to a file."""
    with open(out_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size):
            if chunk:
                f.write(chunk)

def download_scan(host, scan_id, out_dir, uhid=None):
    """Downloads a scan by its ID and saves it."""
    url = f"{host.rstrip('/')}/api/scans/download/{scan_id}"
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            if r.ok:
                disp = r.headers.get('Content-Disposition', '')
                if 'filename=' in disp:
                    fname = disp.split('filename=')[-1].strip(' "')
                else:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    fname = f"{uhid or 'scan'}_{scan_id}_{ts}.dcm"
                
                out_path = os.path.join(out_dir, fname)
                save_stream_to_file(r, out_path)
                return fname
            else:
                return None
    except Exception:
        return None

def poll_request_status(host, request_id, timeout_s, poll_interval_s, out_dir, uhid):
    """Polls the status of a request until it's attended or times out."""
    status_url = f"{host.rstrip('/')}/api/request_status/{request_id}"
    started = time.time()
    while time.time() - started < timeout_s:
        try:
            r = requests.get(status_url, timeout=15)
            if r.ok:
                j = r.json()
                status = j.get('status')
                scan_id = j.get('scan_id')
                if status and status.lower() in ('attended', 'completed') and scan_id:
                    return download_scan(host, scan_id, out_dir, uhid)
        except requests.RequestException:
            # Ignore connection errors and continue polling
            pass
        time.sleep(poll_interval_s)
    return None

def perform_request(host, department, uhid, scan_type, body_part, poll_interval_s=3.0, timeout_s=300.0):
    """Performs the API request to get or request a scan."""
    url = f"{host.rstrip('/')}/api/v1/get_or_request_scan"
    payload = {
        "department_name": department,
        "uhid": uhid,
        "type_of_scan": scan_type,
        "body_part": body_part
    }
    headers = {'Accept': 'application/json, application/dicom, */*'}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30, stream=True)
    except requests.RequestException as e:
        return None, f"Request error: {e}"

    if resp.status_code == 200:
        if 'application/json' in resp.headers.get('Content-Type', '').lower():
            return None, f"Received unexpected JSON: {resp.json()}"
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"{uhid or 'scan'}_{ts}.dcm"
            out_path = os.path.join("downloads", fname)
            save_stream_to_file(resp, out_path)
            return fname, None

    if resp.status_code == 202:
        j = resp.json()
        request_id = j.get('request_id') or j.get('id')
        if not request_id:
            return None, f"Server returned 202 but no request_id was found: {j}"
        
        fname = poll_request_status(host, request_id, timeout_s, poll_interval_s, "downloads", uhid)
        if fname:
            return fname, None
        else:
            return None, "Polling timed out or the final download failed."

    return None, f"Server returned error {resp.status_code}: {resp.text[:400]}"

# --- Flask UI ---

def generate_test_form_html():
    """Generate the HTML form with proper test categories."""
    html = """<!DOCTYPE html>
<html>
<head>
    <title>Laboratory Test Request System</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/feather-icons"></script>
</head>
<body class="bg-gray-100 min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <!-- Header -->
        <div class="text-center mb-8">
            <h1 class="text-4xl font-bold text-gray-800 mb-2">Laboratory Test Request System</h1>
            <p class="text-gray-600">Request lab tests from the Central Laboratory Management System</p>
        </div>

        <!-- Main Form -->
        <div class="max-w-4xl mx-auto">
            <div class="bg-white shadow-lg rounded-xl p-8 mb-8">
                <h2 class="text-2xl font-semibold mb-6 text-gray-800">Test Request Form</h2>
                
                <form method="POST" class="space-y-6">
                    <!-- Department Display -->
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">Requesting Department</label>
                        <div class="w-full border border-gray-300 rounded-lg px-4 py-3 bg-gray-50 flex justify-between items-center">
                            <span class="font-medium capitalize">{{ department }}</span>
                            <a href="/logout" class="text-sm text-blue-600 hover:underline">Change</a>
                        </div>
                    </div>

                    <!-- UHID -->
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">Patient UHID</label>
                        <input type="text" name="uhid" class="w-full border border-gray-300 rounded-lg px-4 py-3 focus:ring-2 focus:ring-blue-500 focus:border-blue-500" placeholder="Enter Patient UHID" required>
                    </div>

                    <!-- Priority -->
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">Priority</label>
                        <select name="priority" class="w-full border border-gray-300 rounded-lg px-4 py-3 focus:ring-2 focus:ring-blue-500 focus:border-blue-500" required>
                            <option value="routine">Routine</option>
                            <option value="urgent">Urgent</option>
                            <option value="stat">STAT (Immediate)</option>
                        </select>
                    </div>

                    <!-- Specimen -->
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">Specimen Type</label>
                        <input type="text" name="specimen" class="w-full border border-gray-300 rounded-lg px-4 py-3 focus:ring-2 focus:ring-blue-500 focus:border-blue-500" placeholder="e.g., Blood, Urine, Tissue" value="Blood" required>
                    </div>

                    <!-- Clinical Notes -->
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">Clinical Notes</label>
                        <textarea name="clinical_notes" rows="3" class="w-full border border-gray-300 rounded-lg px-4 py-3 focus:ring-2 focus:ring-blue-500 focus:border-blue-500" placeholder="Enter any clinical notes or special instructions"></textarea>
                    </div>

                    <!-- Test Selection -->
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-4">Select Tests</label>"""
    
    # Add Biochemistry Tests
    html += """
                        <!-- Biochemistry Tests -->
                        <div class="mb-6">
                            <h3 class="text-lg font-medium text-gray-800 mb-3 flex items-center">
                                <i data-feather="flask" class="w-5 h-5 mr-2 text-blue-600"></i>
                                Biochemistry Tests
                            </h3>
                            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">"""
    
    for category, tests in TEST_CATEGORIES['biochemistry'].items():
        html += f"""
                                <div class="border border-gray-200 rounded-lg p-4">
                                    <h4 class="font-medium text-gray-700 mb-2">{category}</h4>
                                    <div class="space-y-2">"""
        for test in tests:
            html += f"""
                                        <label class="flex items-center">
                                            <input type="checkbox" name="tests" value="{test}" class="rounded border-gray-300 text-blue-600 focus:ring-blue-500 test-checkbox" data-category="{category}" data-test="{test}">
                                            <span class="ml-2 text-sm text-gray-600">{test}</span>
                                        </label>"""
        html += """
                                    </div>
                                </div>"""
    
    # Add Microbiology Tests
    html += """
                        </div>

                        <!-- Microbiology Tests -->
                        <div class="mb-6">
                            <h3 class="text-lg font-medium text-gray-800 mb-3 flex items-center">
                                <i data-feather="microscope" class="w-5 h-5 mr-2 text-green-600"></i>
                                Microbiology Tests
                            </h3>
                            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">"""
    
    for category, tests in TEST_CATEGORIES['microbiology'].items():
        html += f"""
                                <div class="border border-gray-200 rounded-lg p-4">
                                    <h4 class="font-medium text-gray-700 mb-2">{category}</h4>
                                    <div class="space-y-2">"""
        for test in tests:
            html += f"""
                                        <label class="flex items-center">
                                            <input type="checkbox" name="tests" value="{test}" class="rounded border-gray-300 text-blue-600 focus:ring-blue-500">
                                            <span class="ml-2 text-sm text-gray-600">{test}</span>
                                        </label>"""
        html += """
                                    </div>
                                </div>"""
    
    # Add Pathology Tests
    html += """
                        </div>

                        <!-- Pathology Tests -->
                        <div class="mb-6">
                            <h3 class="text-lg font-medium text-gray-800 mb-3 flex items-center">
                                <i data-feather="activity" class="w-5 h-5 mr-2 text-red-600"></i>
                                Pathology Tests
                            </h3>
                            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">"""
    
    for category, tests in TEST_CATEGORIES['pathology'].items():
        html += f"""
                                <div class="border border-gray-200 rounded-lg p-4">
                                    <h4 class="font-medium text-gray-700 mb-2">{category}</h4>
                                    <div class="space-y-2">"""
        for test in tests:
            html += f"""
                                        <label class="flex items-center">
                                            <input type="checkbox" name="tests" value="{test}" class="rounded border-gray-300 text-blue-600 focus:ring-blue-500">
                                            <span class="ml-2 text-sm text-gray-600">{test}</span>
                                        </label>"""
        html += """
                                    </div>
                                </div>"""
    
    # Complete the form
    html += """
                        </div>
                    </div>

                    <!-- Submit Button -->
                    <div class="flex items-center justify-between">
                        <button type="submit" class="bg-blue-600 text-white px-6 py-3 rounded-lg hover:bg-blue-700 focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-colors">
                            <i data-feather="send" class="w-5 h-5 mr-2 inline"></i>
                            Submit Test Request
                        </button>
                        <a href="/history" class="text-blue-600 underline">View History</a>
                    </div>
                </form>
            </div>

            <!-- Results Section -->
            <div id="resultsSection" style="display: none;">
                <div class="bg-white shadow-lg rounded-xl p-8 mb-8">
                    <h2 class="text-2xl font-semibold mb-6 text-gray-800">Order Status</h2>
                    <div id="orderStatus" class="border w-full p-4 bg-gray-50 rounded">
                        <input type="hidden" id="currentOrderId" />
                        <div class="flex items-center justify-between">
                            <div>
                                <strong>Order ID:</strong> <span id="orderIdDisplay"></span><br>
                                <strong>Status:</strong> <span id="currentStatus" class="text-blue-600 font-medium">Queued</span>
                            </div>
                            <div class="flex space-x-2">
                                <button onclick="checkStatus()" class="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600">
                                    Check Status
                                </button>
                                <a id="viewResultsBtn" href="#" class="hidden bg-indigo-500 text-white px-4 py-2 rounded hover:bg-indigo-600">View Results</a>
                                <a id="downloadOrderBtn" href="#" class="bg-green-500 text-white px-4 py-2 rounded hover:bg-green-600 inline-block">Download Order</a>
                            </div>
                        </div>
                        <div id="statusDetails" class="mt-3"></div>
                    </div>
                </div>
            </div>

            <!-- Instructions -->
            <div class="bg-blue-50 border border-blue-200 rounded-lg p-6">
                <h3 class="text-lg font-medium text-blue-800 mb-3 flex items-center">
                    <i data-feather="info" class="w-5 h-5 mr-2"></i>
                    How to Use This System
                </h3>
                <div class="text-blue-700 space-y-2">
                    <p>1. <strong>Select your department</strong> from the dropdown menu</p>
                    <p>2. <strong>Enter the patient's UHID</strong> (Unique Hospital ID)</p>
                    <p>3. <strong>Choose the priority level</strong> (Routine, Urgent, or STAT)</p>
                    <p>4. <strong>Select the specimen type</strong> (Blood, Urine, Tissue, etc.)</p>
                    <p>5. <strong>Add clinical notes</strong> if needed</p>
                    <p>6. <strong>Check the tests you want</strong> from the available categories</p>
                    <p>7. <strong>Submit the request</strong> - the system will automatically check for results</p>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Initialize Feather icons
        feather.replace();
        
        // Form validation
        document.querySelector('form').addEventListener('submit', function(e) {
            const selectedTests = document.querySelectorAll('input[name="tests"]:checked');
            if (selectedTests.length === 0) {
                e.preventDefault();
                alert('Please select at least one test.');
                return false;
            }
        });

        // Status checking function
        function checkStatus() {
            const orderId = document.getElementById('currentOrderId').value;
            if (!orderId) return;

            // Show loading state
            const statusButton = event.target;
            const originalText = statusButton.innerHTML;
            statusButton.innerHTML = '<i data-feather="loader" class="w-4 h-4 mr-2 animate-spin"></i>Checking...';
            statusButton.disabled = true;
            feather.replace();

            fetch(`/api/status/${orderId}`)
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        alert('Error checking status: ' + data.error);
                    } else {
                        // Update the status display
                        updateStatusDisplay(data);
                    }
                })
                .catch(error => {
                    alert('Error checking status: ' + error.message);
                })
                .finally(() => {
                    // Restore button state
                    statusButton.innerHTML = originalText;
                    statusButton.disabled = false;
                    feather.replace();
                });
        }

        function updateStatusDisplay(data) {
            const statusText = document.getElementById('currentStatus');
            const statusDetails = document.getElementById('statusDetails');
            const viewBtn = document.getElementById('viewResultsBtn');
            const orderId = document.getElementById('currentOrderId').value;
            
            if (statusText) {
                if (data.status === 'completed') {
                    statusText.textContent = 'Completed';
                    statusText.className = 'text-green-600 font-medium';
                    if (viewBtn && orderId) {
                        viewBtn.href = `/results/${orderId}`;
                        viewBtn.classList.remove('hidden');
                    }
                    // Show completed departments
                    statusDetails.innerHTML = `
                        <div class="mt-3 p-3 bg-green-100 rounded border border-green-300">
                            <h5 class="font-medium text-green-800 mb-2">✅ Completed Tests:</h5>
                            ${data.completedDepartments.map(dept => 
                                `<div class="text-sm text-green-700 mb-1">🏥 ${dept.department}: ${dept.results.length} results available</div>`
                            ).join('')}
                        </div>
                    `;
                } else {
                    statusText.textContent = 'In Progress';
                    statusText.className = 'text-yellow-600 font-medium';
                    if (viewBtn) viewBtn.classList.add('hidden');
                    // Show all departments and their status
                    statusDetails.innerHTML = `
                        <div class="mt-3 p-3 bg-yellow-100 rounded border border-yellow-300">
                            <h5 class="font-medium text-yellow-800 mb-2">🔄 Department Status:</h5>
                            ${data.allDepartments.map(dept => {
                                const statusIcon = dept.status === 'completed' ? '✅' : dept.status === 'in_progress' ? '🔄' : '⏳';
                                const statusColor = dept.status === 'completed' ? 'text-green-700' : dept.status === 'in_progress' ? 'text-yellow-700' : 'text-gray-700';
                                return `<div class="text-sm ${statusColor} mb-1">${statusIcon} ${dept.department}: ${dept.status}</div>`;
                            }).join('')}
                        </div>
                    `;
                }
            }
        }

        // Test selection logic - automatically select related tests
        document.addEventListener('DOMContentLoaded', function() {
            const testCheckboxes = document.querySelectorAll('.test-checkbox');
            
            testCheckboxes.forEach(checkbox => {
                checkbox.addEventListener('change', function() {
                    const category = this.dataset.category;
                    const test = this.dataset.test;
                    const isChecked = this.checked;
                    
                    // Define related tests for each category
                    const relatedTests = {
                        'Kidney Function': ['GLU', 'UREA', 'CREATININE'],
                        'Liver Function': ['SGOT', 'SGPT', 'ALBUMIN', 'TOTAL_BILIRUBIN'],
                        'Thyroid Function': ['TSH', 'T3', 'T4'],
                        'Lipid Profile': ['TOTAL_CHOLESTEROL', 'HDL', 'LDL']
                    };
                    
                    if (isChecked && relatedTests[category]) {
                        // When a test is selected, automatically select all tests in that category
                        relatedTests[category].forEach(relatedTest => {
                            const relatedCheckbox = document.querySelector(`input[value="${relatedTest}"]`);
                            if (relatedCheckbox && !relatedCheckbox.checked) {
                                relatedCheckbox.checked = true;
                                // Add visual indication that this was auto-selected
                                relatedCheckbox.classList.add('auto-selected');
                                const label = relatedCheckbox.nextElementSibling;
                                if (label) {
                                    label.innerHTML = `${label.textContent} <span class="text-xs text-gray-500">(auto-selected)</span>`;
                                }
                            }
                        });
                    } else if (!isChecked && relatedTests[category]) {
                        // When a test is unchecked, uncheck all tests in that category
                        relatedTests[category].forEach(relatedTest => {
                            const relatedCheckbox = document.querySelector(`input[value="${relatedTest}"]`);
                            if (relatedCheckbox) {
                                relatedCheckbox.checked = false;
                                relatedCheckbox.classList.remove('auto-selected');
                                const label = relatedCheckbox.nextElementSibling;
                                if (label) {
                                    label.innerHTML = label.textContent.replace(' <span class="text-xs text-gray-500">(auto-selected)</span>', '');
                                }
                            }
                        });
                    }
                });
            });
        });

        // Function to show results section after form submission
        function showResultsSection(orderId) {
            document.getElementById('resultsSection').style.display = 'block';
            document.getElementById('currentOrderId').value = orderId;
            document.getElementById('orderIdDisplay').textContent = orderId;
            document.getElementById('downloadOrderBtn').href = `/download/${orderId}`;
            
            // Scroll to results section
            document.getElementById('resultsSection').scrollIntoView({ behavior: 'smooth' });
        }
    </script>
</body>
</html>"""
    
    return html

HTML_FORM = generate_test_form_html()

# Create a login page for department selection
def generate_login_html(error=None):
    """Generate the HTML for the department login page."""
    html = """<!DOCTYPE html>
<html>
<head>
    <title>Department Login</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/feather-icons"></script>
</head>
<body class="bg-gray-100 min-h-screen flex items-center justify-center">
    <div class="max-w-md w-full bg-white rounded-lg shadow-lg p-8">
        <div class="text-center mb-8">
            <h1 class="text-3xl font-bold text-gray-800 mb-2">Department Login</h1>
            <p class="text-gray-600">Select your department to access the Laboratory Test Request System</p>
        </div>
        
        <form method="POST" action="/test_login" class="space-y-6">
            <!-- Department Selection -->
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-2">Department</label>
                <select name="department" class="w-full border border-gray-300 rounded-lg px-4 py-3 focus:ring-2 focus:ring-blue-500 focus:border-blue-500" required>
                    <option value="" disabled selected>Select Your Department</option>
                    <option value="ophthalmology">Ophthalmology</option>
                    <option value="surgery">Surgery</option>
                    <option value="cardiology">Cardiology</option>
                    <option value="neurology">Neurology</option>
                    <option value="orthopedics">Orthopedics</option>
                    <option value="pediatrics">Pediatrics</option>
                    <option value="emergency">Emergency</option>
                    <option value="icu">ICU</option>
                    <option value="general">General Medicine</option>
                </select>
            </div>
            
            <!-- Submit Button -->
            <div>
                <button type="submit" class="w-full bg-blue-600 text-white px-6 py-3 rounded-lg hover:bg-blue-700 focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-colors">
                    <i data-feather="log-in" class="w-5 h-5 mr-2 inline"></i>
                    Login
                </button>
            </div>
        </form>
        
        <!-- Error Message -->
        {% if error %}
        <div class="mt-6 p-4 bg-red-100 border rounded text-red-700">
            <strong>Error:</strong> {{ error }}
        </div>
        {% endif %}
    </div>
    
    <script>
        // Initialize Feather icons
        feather.replace();
    </script>
</body>
</html>"""
    
    # If there was an error, add it to the page
    if error:
        html = html.replace('{% if error %}', '')
        html = html.replace('{% endif %}', '')
        html = html.replace('{{ error }}', error)
    else:
        html = html.replace('{% if error %}\n        <div class="mt-6 p-4 bg-red-100 border rounded text-red-700">\n            <strong>Error:</strong> {{ error }}\n        </div>\n        {% endif %}', '')
    
    return html


# --- API Endpoints for Administration Team ---

@app.route('/api/patient/<string:uhid>', methods=['GET'])
@validate_api_key
def get_patient_api(uhid):
    """
    API endpoint to retrieve patient medical records by UHID.
    This now only returns medical data and prescriptions.
    """
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed."}), 500

    cursor = conn.cursor()
    try:
        # First, check if the patient exists in the patients table
        cursor.execute("SELECT uhid FROM patients WHERE uhid = %s", (uhid,))
        if not cursor.fetchone():
            return jsonify({"error": "Patient not found"}), 404
        
        # Get the department name from the API key
        api_key = request.headers.get("X-API-Key")
        department_name = API_KEYS.get(api_key, "Unknown Department")

        # Now, fetch all medical records for the given UHID, including all test results and prescriptions.
        cursor.execute(
            """SELECT uhid, diagnosis, treatment, visit_date, test_results FROM patient_medical_records
               WHERE uhid = %s ORDER BY visit_date DESC""",
            (uhid,)
        )
        medical_records_data = cursor.fetchall()
        
        # 2. Fetch Prescriptions
        cursor.execute(
            """SELECT uhid, visit_date, spectacle_lens, lens_type, medications, systemic_medication, surgery_recommendation, iol_notes, patient_instructions, follow_up_date 
               FROM patient_prescriptions
               WHERE uhid = %s ORDER BY visit_date DESC""",
            (uhid,)
        )
        prescriptions_data = cursor.fetchall()
        
        # MODIFICATION END

        # 3. Process and Combine Data

        # Organize medical records by visit_date for easier merging with prescriptions
        records_by_date = {}
        for record in medical_records_data:
            uhid, diagnosis, treatment, visit_date, test_results = record
            
            # Use visit_date as the key
            date_key = visit_date.isoformat() if visit_date else None
            
            if date_key not in records_by_date:
                # Initialize the main record structure
                records_by_date[date_key] = {
                    "uhid": uhid,
                    "record_date": date_key,
                    "diagnosis": diagnosis,
                    "treatment": treatment,
                    "test_results": test_results,
                    "prescriptions": []  # List to hold all prescriptions for this visit
                }
        
        # Add prescriptions to the corresponding medical record
        for record in prescriptions_data:
            uhid, visit_date, spectacle_lens, lens_type, medications, systemic_medication, surgery_recommendation, iol_notes, patient_instructions, follow_up_date  = record
            date_key = visit_date.isoformat() if visit_date else None
            
            if date_key in records_by_date:
                records_by_date[date_key]["prescriptions"].append({
                    "spectacle_lens": spectacle_lens,
                    "lens_type": lens_type,
                    "medications": medications,
                    "systemic_medication": systemic_medication,
                    "surgery_recommendation": surgery_recommendation,
                    "iol_notes": iol_notes,
                    "patient_instructions": patient_instructions,
                    "follow_up_date": follow_up_date
                })
            # NOTE: If a prescription exists without a corresponding medical record, 
            # it will be ignored in this structure.

        # Convert the dictionary values back to a list, sorted by date
        combined_records = sorted(
            list(records_by_date.values()), 
            key=lambda x: x['record_date'], 
            reverse=True
        )

        # Return the medical records and the department name
        response = {
            "department": department_name,
            "patient_records": combined_records # Changed key for clarity
        }

        return jsonify(response)

    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        cursor.close()
        conn.close()

@app.route('/api/patient/add', methods=['POST'])
@validate_api_key
def add_patient_api():
    """
    API endpoint to add a new patient to the database.
    This endpoint has been modified to handle only demographic data, as requested,
    and now explicitly rejects attempts to add medical records.
    """
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed."}), 500

    cursor = conn.cursor()
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON data"}), 400

        # Required demographic fields
        demographics = data.get('demographics', {})
        uhid = demographics.get('uhid')
        first_name = demographics.get('first_name')
        last_name = demographics.get('last_name')
        dob = demographics.get('dob')
        gender = demographics.get('gender')
        address = demographics.get('address')
        phone = demographics.get('phone')
        email = demographics.get('email')

        if not all([uhid, first_name, last_name, dob]):
            return jsonify({"error": "Missing required demographic fields: uhid, first_name, last_name, and dob are mandatory."}), 400
        
        # Check if the patient already exists
        cursor.execute("SELECT uhid FROM patients WHERE uhid = %s", (uhid,))
        if cursor.fetchone():
            return jsonify({"error": f"Patient with UHID {uhid} already exists."}), 409

        # Insert the new patient's demographic data
        cursor.execute(
            """INSERT INTO patients (uhid, first_name, last_name, dob, gender, address, phone, email)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (uhid, first_name, last_name, dob, gender, address, phone, email)
        )
        patient_id = cursor.fetchone()[0]

        conn.commit()

        # Check for and reject medical record data
        if 'medical_records' in data:
            return jsonify({
                "message": "Patient added successfully! Note: No medical records were added as this is restricted to the administration department.",
                "uhid": uhid
            }), 201
        
        return jsonify({
            "message": "Patient added successfully!",
            "uhid": uhid
        }), 201

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        cursor.close()
        conn.close()

# --- Decorators for Authentication and Authorization ---
def login_required(f):
    """Decorator to ensure user is logged in."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(role):
    """Decorator to ensure user has a specific role."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_role' not in session or session['user_role'] != role:
                flash(f"Access denied. You need '{role}' privileges.", "danger")
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# --- Routes ---

@app.route('/')
def index():
    """Render the front page of the application."""
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login."""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, password_hash, role FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            cursor.close()
            conn.close()

            if user and check_password_hash(user[2], password):
                session['user_id'] = user[0]
                session['username'] = user[1]
                session['user_role'] = user[3]
                flash(f"Welcome, {user[1]}! You are logged in as {user[3]}.", "success")
                return redirect(url_for('dashboard'))
            else:
                flash("Invalid username or password.", "danger")
        else:
            flash("Could not connect to database for login.", "error")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Handle user logout."""
    session.pop('user_id', None)
    session.pop('username', None)
    session.pop('user_role', None)
    flash("You have been logged out.", "info")

    response = make_response(redirect(url_for('index')))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/dashboard')
@login_required
def dashboard():
    """Render the main dashboard based on user role."""
    if session['user_role'] == 'admin':
        return render_template('admin_panel.html', username=session['username'])
    else:
        patients = []
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """SELECT uhid, first_name, last_name, dob, gender FROM patients
                       ORDER BY last_name, first_name"""
                )
                patients = cursor.fetchall()
            except Exception as e:
                flash(f"Error fetching patient list: {e}", "danger")
            finally:
                cursor.close()
                conn.close()
        return render_template('dashboard.html', username=session['username'], role=session['user_role'], patients=patients)

@app.route('/add_patient', methods=['POST'])
@login_required
def add_patient():
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed.', 'danger')
        return redirect(url_for('dashboard'))

    cursor = conn.cursor()
    
    uhid = request.form.get('uhid')
    first_name = request.form.get('first_name')
    last_name = request.form.get('last_name')
    dob = request.form.get('dob')
    gender = request.form.get('gender')
    address = request.form.get('address')
    phone = request.form.get('phone')
    email = request.form.get('email')

    # Basic validation
    if not uhid or not first_name or not last_name:
        flash('UHID, First Name, and Last Name are required.', 'danger')
        return redirect(url_for('dashboard'))

    try:
        # Check if MRN already exists
        cursor.execute("SELECT COUNT(*) FROM patients WHERE uhid = %s", (uhid,))
        if cursor.fetchone()[0] > 0:
            flash(f'A patient with UHID {uhid} already exists.', 'warning')
            return redirect(url_for('dashboard'))

        cursor.execute(
            """INSERT INTO patients (uhid, first_name, last_name, dob, gender, address, phone, email)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (uhid, first_name, last_name, dob, gender, address, phone, email)
        )
        conn.commit()

        # Get the ID of the newly created patient
        cursor.execute("SELECT uhid FROM patients WHERE uhid = %s", (uhid,))
        uhid = cursor.fetchone()[0]

        # Log the action in edit history
        cursor.execute(
            """INSERT INTO patient_edit_history (uhid, editor_id, field_name, new_value, edited_at)
               VALUES (%s, %s, %s, %s, NOW())""",
            (uhid, session['user_id'], 'new_patient_added', f"New patient added: {first_name} {last_name}",)
        )
        conn.commit()

        flash('Patient added successfully!', 'success')
    except psycopg2.Error as e:
        conn.rollback()
        flash(f'Error adding patient: {str(e)}', 'danger')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('dashboard'))


# --- Admin Routes ---
@app.route('/admin/create_user', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def create_user():
    """Admin functionality to create new doctor or nurse users."""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']
        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
                    (username, hashed_password, role)
                )
                conn.commit()
                flash(f"User '{username}' ({role}) created successfully!", "success")
                # Audit log for user creation - NO patient_id associated
                cursor.execute(
                    """INSERT INTO patient_edit_history (patient_id, editor_id, field_name, old_value, new_value, edited_at)
                       VALUES (%s, %s, %s, %s, %s, NOW())""",
                    (None, session['user_id'], 'user_creation', None, f"Created user: {username} ({role})")
                )
                conn.commit()
            except psycopg2.IntegrityError:
                flash("Username already exists. Please choose a different one.", "danger")
                conn.rollback()
            except Exception as e:
                flash(f"Error creating user: {e}", "danger")
                conn.rollback()
            finally:
                cursor.close()
                conn.close()
    return render_template('admin_panel.html', username=session['username'])

@app.route('/admin/audit_logs')
@login_required
@role_required('admin')
def audit_logs():
    """Admin functionality to view audit logs with date and time filters."""
    conn = get_db_connection()
    logs = []
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    if conn:
        cursor = conn.cursor()
        query_base = """
            SELECT
                peh.edited_at, u.username as editor_username,
                COALESCE(p.mrn, 'System Event') as mrn,
                COALESCE(p.first_name, '') as first_name,
                COALESCE(p.last_name, '') as last_name,
                peh.field_name, peh.old_value, peh.new_value
            FROM patient_edit_history peh
            LEFT JOIN users u ON peh.editor_id = u.id
            LEFT JOIN patients p ON peh.patient_id = p.id
        """
        where_clauses = []
        query_params = []

        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%dT%H:%M')
                where_clauses.append("peh.edited_at >= %s")
                query_params.append(start_date)
            except ValueError:
                flash("Invalid 'From' date format. Please use the calendar picker.", "danger")
                start_date_str = ''

        if end_date_str:
            try:
                end_date = datetime.strptime(end_date_str, '%Y-%m-%dT%H:%M')
                where_clauses.append("peh.edited_at <= %s")
                query_params.append(end_date)
            except ValueError:
                flash("Invalid 'To' date format. Please use the calendar picker.", "danger")
                end_date_str = ''

        final_query = query_base
        if where_clauses:
            final_query += " WHERE " + " AND ".join(where_clauses)
        final_query += " ORDER BY peh.edited_at DESC"
        print("Executing Query:")
        print(final_query)
        print("With Parameters:")
        print(query_params)
        try:
            cursor.execute(final_query, query_params)
            logs = cursor.fetchall()
            print("Fetched Logs:")
            print(logs)
        except Exception as e:
            flash(f"Error fetching audit logs: {e}", "danger")
            print(f"Error fetching audit logs: {e}")
            conn.rollback()
        finally:
            cursor.close()
            conn.close()
    else:
        flash("Could not connect to database to fetch audit logs.", "error")

    return render_template('admin_panel.html', username=session['username'], audit_logs=logs,
                           start_date=start_date_str, end_date=end_date_str, show_audit_logs_section=True)

@app.route('/admin/audit_logs/download')
@login_required
@role_required('admin')
def download_audit_logs():
    """Admin functionality to download audit logs as CSV with date and time filters."""
    conn = get_db_connection()
    logs = []
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    if conn:
        cursor = conn.cursor()
        query_base = """
            SELECT
                peh.edited_at, u.username as editor_username,
                COALESCE(p.mrn, 'System Event') as mrn,
                COALESCE(p.first_name, '') as first_name,
                COALESCE(p.last_name, '') as last_name,
                peh.field_name, peh.old_value, peh.new_value
            FROM patient_edit_history peh
            LEFT JOIN users u ON peh.editor_id = u.id
            LEFT JOIN patients p ON peh.patient_id = p.id
        """
        where_clauses = []
        query_params = []

        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%dT%H:%M')
                where_clauses.append("peh.edited_at >= %s")
                query_params.append(start_date)
            except ValueError:
                flash("Invalid 'From' date format. Please use the calendar picker.", "danger")
                start_date_str = ''

        if end_date_str:
            try:
                end_date = datetime.strptime(end_date_str, '%Y-%m-%dT%H:%M')
                where_clauses.append("peh.edited_at <= %s")
                query_params.append(end_date)
            except ValueError:
                flash("Invalid 'To' date format. Please use the calendar picker.", "danger")
                end_date_str = ''

        final_query = query_base
        if where_clauses:
            final_query += " WHERE " + " AND ".join(where_clauses)
        final_query += " ORDER BY peh.edited_at DESC"

        try:
            cursor.execute(final_query, query_params)
            logs = cursor.fetchall()
        except Exception as e:
            flash(f"Error fetching audit logs: {e}", "danger")
            conn.rollback()
        finally:
            cursor.close()
            conn.close()
    else:
        flash("Could not connect to database to fetch audit logs.", "error")
        return redirect(url_for('audit_logs', show_logs='true'))

    # Create CSV content
    import csv
    from io import StringIO
    
    output = StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['Timestamp', 'Editor', 'Patient MRN', 'First Name', 'Last Name', 'Field', 'Old Value', 'New Value'])
    
    # Write data
    for log in logs:
        writer.writerow(log)
    
    # Prepare response
    output.seek(0)
    
    # Create filename with timestamp
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"audit_logs_{timestamp}.csv"
    
    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    response.headers['Content-type'] = 'text/csv'
    
    return response

@app.route('/patient/search', methods=['GET', 'POST'])
@login_required
def search_patient():
    """Search for a patient by MRN or Name."""
    if session['user_role'] == 'admin':
        flash("Admin users do not have access to patient records.", "danger")
        return redirect(url_for('dashboard'))

    patients = []
    # Handle both GET and POST methods
    if request.method == 'POST':
        search_query = request.form.get('search_query', '')
    else:
        search_query = request.args.get('search_query', '')
    
    conn = get_db_connection()
    if not conn:
        flash("Database connection failed.", "error")
        return render_template('dashboard.html', patients=patients, search_query=search_query, role=session['user_role'])
    
    cursor = conn.cursor()
    try:
        if search_query:
            search_pattern = f"%{search_query}%"
            cursor.execute(
                """SELECT uhid, first_name, last_name, dob, gender FROM patients
                   WHERE uhid ILIKE %s OR first_name ILIKE %s OR last_name ILIKE %s
                   ORDER BY last_name, first_name""",
                (search_pattern, search_pattern, search_pattern)
            )
            patients = cursor.fetchall()
            if not patients:
                flash(f"No patients found for '{search_query}'.", "info")
        else:
            cursor.execute(
                """SELECT uhid, first_name, last_name, dob, gender FROM patients
                   ORDER BY last_name, first_name"""
            )
            patients = cursor.fetchall()
    except Exception as e:
        flash(f"Error searching patients: {e}", "danger")
    finally:
        cursor.close()
        conn.close()

    return render_template('dashboard.html', patients=patients, search_query=search_query, role=session['user_role'])

@app.route('/patient/<string:uhid>', methods=['GET', 'POST'])
@login_required
def view_patient(uhid):
    """View and edit patient details, medical records, and history."""
    if session['user_role'] == 'admin':
        flash("Access denied. Admin users do not have access to patient records.", "danger")
        return redirect(url_for('dashboard'))

    conn = get_db_connection()
    if not conn:
        flash("Database connection failed.", "error")
        return redirect(url_for('dashboard'))
    
    patient = None
    medical_records = []
    today_date = datetime.now().date().strftime('%Y-%m-%d')
    
    cursor = conn.cursor()

    try:
        # First, get the patient's demographic information
        cursor.execute("SELECT * FROM patients WHERE uhid = %s", (uhid,))
        patient = cursor.fetchone()

        if not patient:
            flash("Patient not found.", "danger")
            return redirect(url_for('dashboard'))

       
        # Then, use the correct 'uhid' column to query for medical records
        cursor.execute(
            """SELECT uhid, visit_date, diagnosis, treatment, test_results, created_by, created_at, updated_at
               FROM patient_medical_records WHERE uhid = %s ORDER BY visit_date DESC""",
            (uhid,)
        )
        raw_medical_records = cursor.fetchall()

        # Process medical records to handle JSON data
        for record_row in raw_medical_records:
            record_list = list(record_row)
            test_results_from_db = record_list[4]
            # Safely process test results, assuming they might be a string or dict
            processed_test_results = test_results_from_db if isinstance(test_results_from_db, dict) else {}
            record_list[4] = processed_test_results
            medical_records.append(tuple(record_list))
            
        # Handle POST requests for updating patient details or adding medical records
        if request.method == 'POST':
            if session['user_role'] not in ['doctor', 'nurse']:
                flash("Access denied. Only doctors can add medical records.", "danger")
                return redirect(url_for('view_patient', uhid=uhid))
            
            # Logic for adding a new medical record
            is_medical_record_form = 'diagnosis' in request.form and 'treatment' in request.form

            if is_medical_record_form:
                visit_date = request.form['visit_date']
                diagnosis = request.form['diagnosis']
                treatment = request.form['treatment']

                # Debug what we're receiving
                print("=== MEDICAL RECORD FORM DATA ===")
                print("All form fields:", list(request.form.keys()))
                print("test_results received:", 'test_results' in request.form)
                print("test_results value:", request.form.get('test_results'))
    
                
                # Handling eye drop, medication, and surgery data
                # Use hidden JSON (from JS) as source of truth
                test_results_json = request.form.get('test_results', '{}')
                print("Raw test_results_json:", test_results_json)
                try:
                    if test_results_json and test_results_json != '{}':
                        test_results_data = json.loads(test_results_json)
                    else:
                        test_results_data = {}
                        print("WARNING: test_results is empty or missing!")
                except Exception as e:
                    print("Error parsing test_results:", e)
                    print("Problematic JSON:", test_results_json)
                    test_results_data = {}

                print("Final test_results_data:", test_results_data)
    
# Merge DR risk separately if submitted
                risk_category = request.form.get('risk_category')
                risk_score = request.form.get('risk_score')
                if risk_category or risk_score:
                    test_results_data['dr_risk_assessment'] = {
                        'risk_category': risk_category,
                        'risk_score': risk_score
                    }


                
                cursor.execute(
                    """INSERT INTO patient_medical_records (uhid, visit_date, diagnosis, treatment, test_results, created_by, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())""",
                    (uhid, visit_date, diagnosis, treatment,  json.dumps(test_results_data), session['user_id'])
                )
                
                # Audit Log for medical record creation
                cursor.execute(
                    """INSERT INTO patient_edit_history (uhid, editor_id, field_name, new_value, edited_at)
                       VALUES (%s, %s, %s, %s, NOW())""",
                    (uhid, session['user_id'], 'medical_record_created', f"New record for visit date: {visit_date}",)
                )
                print("Raw test_results:", request.form.get('test_results'))
                print("Type:", type(request.form.get('test_results')))

                conn.commit()
                flash("Medical record added successfully!", "success")
                return redirect(url_for('view_patient', uhid=uhid))
            
            # Logic for updating patient details (demographics)
            else:
                updated_fields_for_db = {}
                original_patient = {
                    "uhid": patient[0], "first_name": patient[1], "last_name": patient[2],
                    "dob": patient[3], "gender": patient[4], "address": patient[5],
                    "phone": patient[6], "email": patient[7]
                }
                
                # Collect updated fields for audit trail
                for field in original_patient:
                    new_value = request.form.get(field)
                    old_value = original_patient[field]
                    if field == 'dob':
                        old_value = old_value.isoformat() if old_value else ''
                        new_value = new_value.strip() if new_value is not None else ''
                    
                    if str(new_value) != str(old_value):
                        updated_fields_for_db[field] = (old_value, new_value)
                
                if updated_fields_for_db:
                    # Construct dynamic UPDATE query
                    update_query_parts = []
                    update_values = []
                    for field, (old_val, new_val) in updated_fields_for_db.items():
                        update_query_parts.append(f"{field} = %s")
                        update_values.append(new_val)
                    
                    update_values.append(uhid)
                    final_update_query = f"UPDATE patients SET {', '.join(update_query_parts)}, updated_at = NOW() WHERE uhid = %s"

                    try:
                        cursor.execute(final_update_query, update_values)
                        conn.commit()
                        flash("Patient details updated successfully!", "success")

                        # Audit Log for patient details update
                        for field, (old_val, new_val) in updated_fields_for_db.items():
                            cursor.execute(
                                """INSERT INTO patient_edit_history (uhid, editor_id, field_name, old_value, new_value, edited_at)
                                   VALUES (%s, %s, %s, %s, %s, NOW())""",
                                (uhid, session['user_id'], field, old_val, new_val)
                            )
                        conn.commit()
                    except Exception as e:
                        flash(f"Error updating patient details: {e}", "danger")
                        conn.rollback()
                else:
                    flash("No changes detected to update patient details.", "info")
            
            return redirect(url_for('view_patient', uhid=uhid))

    except Exception as e:
        flash(f"Error viewing patient: {e}", "danger")
        if conn:
            conn.rollback()
        return redirect(url_for('dashboard'))
    finally:
        if conn:
            cursor.close()
            conn.close()

    # Create the patient dictionary for the template
    patient_dict = {
        "uhid": patient[0], "first_name": patient[1], "last_name": patient[2],
        "dob": patient[3].strftime('%Y-%m-%d') if patient[3] else '', "gender": patient[4],
        "address": patient[5], "phone": patient[6], "email": patient[7],
        "created_at": patient[8].strftime('%Y-%m-%d %H:%M:%S') if patient[8] else '',
        "updated_at": patient[9].strftime('%Y-%m-%d %H:%M:%S') if patient[9] else ''
    }

    return render_template('patient_view.html', patient=patient_dict, medical_records=medical_records, today_date=today_date, current_role=session['user_role'])



@app.route('/add_medical_record/<string:uhid>', methods=['POST'])
@login_required
@role_required('doctor')
def add_medical_record(uhid):
    conn = get_db_connection()
    if not conn:
        flash("Database connection error.", "danger")
        return redirect(url_for('dashboard'))

    cursor = conn.cursor()
    try:
        # Get patient_id
        cursor.execute("SELECT uhid FROM patients WHERE uhid = %s", (uhid,))
        patient = cursor.fetchone()
        if not patient:
            flash("Patient not found.", "danger")
            return redirect(url_for('dashboard'))
        patient_id = patient[0]

        uhid = request.form.get('uhid')
        visit_date = request.form['visit_date']
        diagnosis = request.form['diagnosis']
        treatment = request.form['treatment']

        # --- 1. Map Clinical Measurements (Ensure Keys Match CSV) ---
        CLINICAL_FIELD_MAP = {
            'va_od': 'VA_OD', 'va_os': 'VA_OS', 
            'va_od_corrected': 'VA_OD_with_correction', 'va_os_corrected': 'VA_OS_with_correction',
            'iop_od': 'IOP_OD', 'iop_os': 'IOP_OS', 
            'ref_od_sph': 'Refraction_OD_Sph', 'ref_od_cyl': 'Refraction_OD_Cyl', 'ref_od_ax': 'Refraction_OD_Ax', 
            'ref_os_sph': 'Refraction_OS_Sph', 'ref_os_cyl': 'Refraction_OS_Cyl', 'ref_os_ax': 'Refraction_OS_Ax', 
            'sle_od_cornea': 'SLE_OD_Cornea', 'sle_os_cornea': 'SLE_OS_Cornea', 
            'sle_od_lens': 'SLE_OD_Lens', 'sle_os_lens': 'SLE_OS_Lens',
            'fundus_od': 'Fundus_OD', 'fundus_os': 'Fundus_OS'
        }
        
        final_test_results = {}
        for form_key, json_key in CLINICAL_FIELD_MAP.items():
            value = request.form.get(form_key)
            if value and value.strip():
                # Safety: Check for numeric fields and attempt conversion
                try:
                    is_numeric_field = any(x in form_key for x in ['iop_', 'ref_'])
                    
                    if is_numeric_field:
                        # Convert to int or float if possible, otherwise keep as string
                        if '.' in value:
                             final_test_results[json_key] = float(value.strip())
                        else:
                            final_test_results[json_key] = int(value.strip())
                    else:
                        final_test_results[json_key] = value.strip()
                except ValueError:
                    # If conversion fails (e.g., 'VA_OD' is '20/20'), save as string.
                    final_test_results[json_key] = value.strip()


        # --- 4. Final Serialization ---
        test_results_json = json.dumps(final_test_results)
    
        print(json.dumps(final_test_results, indent=2))

        # --- 5. Database Insertion/Update ---

        if uhid:
            # Updating an existing record
            cursor.execute(
                """UPDATE patient_medical_records SET
                   uhid=%s,
                   visit_date = %s,
                   diagnosis = %s,
                   treatment = %s,
                   test_results = %s,
                   updated_at = NOW()
                   WHERE uhid = %s""",
                (uhid, visit_date, diagnosis, treatment, test_results_json, uhid)
            )
            flash("Medical record updated successfully!", "success")
        else:
            # Adding a new record
            cursor.execute(
                """INSERT INTO patient_medical_records (
                   uhid, visit_date, diagnosis, treatment, test_results, created_by, created_at, updated_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())""", 
                (uhid, visit_date, diagnosis, treatment, test_results_json, session['user_id'])
            )
            flash("Medical record added successfully!", "success")

        conn.commit()
    except Exception as e:
        conn.rollback()
        # Print the error for debugging your Flask console
        print(f"DATABASE ERROR: {e}")
        flash(f"An error occurred while saving the record. Please check the server logs.", "danger")
        return redirect(url_for('view_patient', uhid=uhid))
    finally:
        cursor.close()
        conn.close()

    # 🚨 Start of FIX: Retrieve the internal patient_id (PK 'id') using the UHID ('mrn')
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
        
        # Query the patients table to get the internal ID (patient_id)
            cursor.execute("SELECT uhid FROM patients WHERE uhid = %s", (uhid,))
            patient_row = cursor.fetchone()
        
            if patient_row:
                uhid = patient_row[0] # The internal ID is the first element
                flash('Medical record added successfully!', 'success')
            
            # 🚨 MODIFIED REDIRECT: Pass both required parameters
                return redirect(url_for('view_patient', uhid=uhid,))
            else:
                flash('Error: Patient not found for this UHID.', 'error')
            # Redirect to a safe page if lookup fails (e.g., all patients list)
                return redirect(url_for('view_all_patients')) 
            
        except Exception as e:
            flash(f'An internal error occurred: {e}', 'error')
            return redirect(url_for('view_all_patients')) 
        finally:
            if conn:
                conn.close()    

    return redirect(url_for('view_patient', uhid=uhid))

@app.route('/view_medical_history/<uhid>', methods=['GET'])
@login_required 
def view_medical_history(uhid):
    conn = None
    cursor = None
    try:
        # Get DB connection
        conn = get_db_connection()
        if not conn:
            flash("Database connection error.", "danger")
            return redirect(url_for('dashboard')) 

        # Use DictCursor for fetching data as dictionaries
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # --- 1. Fetch Patient Data by MRN ---
        cursor.execute(
            "SELECT uhid, first_name, last_name, dob, gender, phone, email, address FROM patients WHERE uhid = %s", 
            (uhid,)
        )
        patient_data = cursor.fetchone()

        if not patient_data:
            flash(f"Patient with ID '{uhid}' not found.", "danger")
            return redirect(url_for('dashboard'))
        
        
        
        # --- 2. Fetch Medical Records ---
        cursor.execute(
            """SELECT uhid, diagnosis, treatment, visit_date, test_results 
                FROM patient_medical_records 
                WHERE uhid = %s 
                ORDER BY visit_date DESC""", 
            (uhid,)
        )
        medical_records = cursor.fetchall()
        
        # --- 3. Fetch Prescriptions ---
        cursor.execute(
            """SELECT 
                uhid, 
                created_at, 
                spectacle_lens,      
                lens_type,               
                medications, 
                systemic_medication, 
                surgery_recommendation, 
                iol_notes, 
                patient_instructions, 
                follow_up_date 
            FROM patient_prescriptions 
            WHERE uhid = %s 
            ORDER BY created_at DESC""", 
            (uhid,)
        )
        prescriptions = cursor.fetchall()
        
        # --- 4. Process Records and Prescriptions ---
        medical_records_list = [dict(row) for row in medical_records]
        prescriptions_list = []

        for row in prescriptions:
            record = dict(row)

    # --- SPECTACLE DATA ---
            try:
                raw_lens = record.pop('spectacle_lens', '{}')
                if isinstance(raw_lens, str):
                    record['spectacle_data'] = json.loads(raw_lens)
                else:
                    record['spectacle_data'] = raw_lens
            except Exception as e:
                print(f"[DEBUG] Error parsing spectacle_lens JSON: {e}")
                record['spectacle_data'] = {}

    # --- MEDICATIONS ---
            try:
                raw_meds = record.pop('medications', '[]')
                if isinstance(raw_meds, str):
                    med_list = json.loads(raw_meds)
                else:
                    med_list = raw_meds
            except Exception as e:
                print(f"[DEBUG] Error parsing medications JSON: {e}")
                med_list = []

            med_strings = []
            for med in med_list:
                name = med.get('name', 'N/A')
                dose = med.get('dose', '')
                freq = med.get('frequency', '')
                eye = med.get('eye', '')
                duration = med.get('duration_value', '')
                unit = med.get('duration_unit', '')
                med_strings.append(f"{name} {dose} {freq} ({eye}) for {duration} {unit}".strip())

            record['medications_text'] = ' | '.join(med_strings)
            prescriptions_list.append(record)

        # Render the template with the fetched data
        return render_template('view_medical_history.html', 
                               patient=dict(patient_data), 
                               medical_records=medical_records_list,
                               prescriptions=prescriptions_list) # <-- NOTE: Using 'prescriptions'

    except Exception as e:
        flash(f"An error occurred while fetching history: {e}", "danger")
        print(f"Error in view_medical_history: {e}")
        return redirect(url_for('dashboard')) 
    finally:
        # Cleanup connection resources
        if cursor and not cursor.closed: cursor.close()
        if conn: conn.close()


@app.route("/scan/<uhid>", methods=["GET", "POST"])
def scan(uhid):
    """Handles the form submission and renders the page."""
    dicom_file, error = None, None
    if request.method == "POST":
        host = DEFAULT_HOST
        department = request.form.get("department", "Ophthamology")
        uhid = request.form.get("uhid")
        scan_type = request.form.get("scan_type")
        body_part = request.form.get("body_part")

        if not all([uhid, scan_type, body_part]):
            error = "UHID, Scan Type, and Body Part are required fields."
        else:
            dicom_file, error = perform_request(host, department, uhid, scan_type, body_part)

    return render_template('scan.html', dicom_file=dicom_file, error=error)

@app.route("/test_login", methods=["GET", "POST"])
def test_login():
    """Handle department login."""
    if request.method == "POST":
        department = request.form.get("department")
        if department:
            session["department"] = department
            return redirect(url_for("test_index"))
        else:
            return generate_login_html(error="Please select a department")
    return generate_login_html()

@app.route("/test_logout")
def test_logout():
    """Handle department logout."""
    session.pop("department", None)
    return redirect(url_for("test_login"))

@app.route("/test_index", methods=["GET", "POST"])
def test_index():
    """Handles the form submission and renders the page."""
    # Check if department is selected
    if "department" not in session:
        return redirect(url_for("test_login"))
        
    order_id, error = None, None
    
    if request.method == "POST":
        host = DEFAULT_HOST
        department = session.get("department")  # Get department from session
        uhid = request.form.get("uhid")
        priority = request.form.get("priority")
        specimen = request.form.get("specimen")
        clinical_notes = request.form.get("clinical_notes")
        tests = request.form.getlist("tests")

        if not all([department, uhid, tests]):
            error = "UHID and at least one test are required fields."
        else:
            order_id, error = perform_test_request(host, department, uhid, tests, priority, specimen, clinical_notes)

    # Get the current department from session
    department = session.get("department")
    
    # Generate the HTML form
    html = generate_test_form_html()
    
    # Replace the department placeholder
    html = html.replace('{{ department }}', department)
    
    # If there was an error, add it to the page
    if error:
        html = html.replace('</form>', f'''
            </form>
            <div class="mt-6 p-4 bg-red-100 border rounded text-red-700">
                <strong>Error:</strong> {error}
            </div>''')
    
    # If order was created successfully, show the results section
    if order_id:
        html = html.replace('id="resultsSection" style="display: none;"', 'id="resultsSection" style="display: block;"')
        html = html.replace('id="currentOrderId" />', f'id="currentOrderId" value="{order_id}" />')
        html = html.replace('id="orderIdDisplay"></span>', f'id="orderIdDisplay">{order_id}</span>')
        html = html.replace('id="downloadOrderBtn" href="#"', f'id="downloadOrderBtn" href="/download/{order_id}"')
        # Add script to scroll to results section
        html = html.replace('</script>', f'''
            // Auto-scroll to results section after successful submission
            setTimeout(() => {{
                document.getElementById('resultsSection').scrollIntoView({{ behavior: 'smooth' }});
            }}, 100);
        </script>''')

    return html

@app.route("/api/status/<order_id>")
def check_order_status(order_id):
    """Check the status of an order."""
    try:
        url = f"{DEFAULT_HOST.rstrip('/')}/api/orders/{order_id}"
        resp = requests.get(url, headers={'X-API-Key': SHARED_API_KEY}, timeout=15)
        
        if resp.status_code == 200:
            order_data = resp.json()
            per_dept = order_data.get('perDepartment', [])
            
            # Check if any department has completed results
            completed_depts = []
            for dept in per_dept:
                if dept.get('status') == 'completed':
                    completed_depts.append({
                        'department': dept.get('department'),
                        'status': dept.get('status'),
                        'results': dept.get('results', [])
                    })
            
            return jsonify({
                'orderId': order_id,
                'status': 'completed' if completed_depts else 'in_progress',
                'completedDepartments': completed_depts,
                'allDepartments': per_dept
            })
        else:
            return jsonify({'error': f'Failed to fetch order status: {resp.status_code}'}), 400
            
    except Exception as e:
        return jsonify({'error': f'Error checking status: {str(e)}'}), 500

@app.route("/api/order/<order_id>")
def api_get_order(order_id):
    try:
        r = requests.get(f"{DEFAULT_HOST.rstrip('/')}/api/orders/{order_id}", headers={'X-API-Key': SHARED_API_KEY}, timeout=20)
        return (r.text, r.status_code, {"Content-Type": r.headers.get('Content-Type', 'application/json')})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/results/<order_id>")
def view_results(order_id):
    # Check if user is logged in with a department
    if 'department' not in session:
        return redirect(url_for('test_login'))
    
    current_department = session['department']
    
    # Check if the order exists in our history and belongs to the current department
    hist = load_history()
    order_exists = False
    order_belongs_to_department = False
    
    for order in hist:
        if order.get("orderId") == order_id:
            order_exists = True
            if order.get("department", "").lower() == current_department.lower():
                order_belongs_to_department = True
            break
    
    if not order_exists:
        error_page = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Error</title>
            <script src="https://cdn.tailwindcss.com"></script>
        </head>
        <body class="bg-gray-100 min-h-screen p-8">
            <div class="max-w-md mx-auto bg-white p-8 rounded-lg shadow-md">
                <h1 class="text-2xl font-bold text-red-600 mb-4">Error</h1>
                <p class="mb-4">Order ID {order_id} not found in history.</p>
                <a href="/history" class="text-blue-600">Return to History</a>
            </div>
        </body>
        </html>
        """
        return error_page, 404
    
    if not order_belongs_to_department:
        error_page = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Access Denied</title>
            <script src="https://cdn.tailwindcss.com"></script>
        </head>
        <body class="bg-gray-100 min-h-screen p-8">
            <div class="max-w-md mx-auto bg-white p-8 rounded-lg shadow-md">
                <h1 class="text-2xl font-bold text-red-600 mb-4">Access Denied</h1>
                <p class="mb-4">You do not have permission to view results for this order.</p>
                <p class="mb-4">This order belongs to another department.</p>
                <a href="/history" class="text-blue-600">Return to History</a>
            </div>
        </body>
        </html>
        """
        return error_page, 403
        
    try:
        r = requests.get(f"{DEFAULT_HOST.rstrip('/')}/api/orders/{order_id}", headers={'X-API-Key': SHARED_API_KEY}, timeout=20)
        if not r.ok:
            error_page = render_template_string("""
            <!DOCTYPE html><html><head><title>Results</title><script src=\"https://cdn.tailwindcss.com\"></script></head>
            <body class=\"bg-gray-100 p-8\"><div class=\"max-w-5xl mx-auto bg-white p-6 rounded shadow\">
            <h1 class=\"text-2xl font-semibold mb-4\">Results</h1>
            <div class=\"text-red-600\">Failed to load results (status: {{status}})</div>
            <a class=\"mt-4 inline-block text-blue-600\" href=\"/\">Back</a></div></body></html>""", status=r.status_code)
            return error_page, r.status_code
        data = r.json()
        return render_template_string("""
<!DOCTYPE html>
<html>
        <head>
            <title>Order {{ data.orderId }} Results</title>
            <script src="https://cdn.tailwindcss.com"></script>
            <script src="https://unpkg.com/feather-icons"></script>
        </head>
        <body class="bg-gray-100 min-h-screen">
            <div class="max-w-6xl mx-auto p-6">
                <div class="mb-6 flex items-center justify-between">
                    <h1 class="text-2xl font-bold">Order Results • {{ data.orderId }}</h1>
                    <a href="/" class="text-blue-600">Back to Request Form</a>
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
                    <div class="bg-white p-4 rounded shadow">
                        <div class="text-sm text-gray-500">Patient</div>
                        <div class="font-medium">{{ data.patient.name if data.patient else 'N/A' }}</div>
                    </div>
                    <div class="bg-white p-4 rounded shadow">
                        <div class="text-sm text-gray-500">Priority</div>
                        <div class="font-medium">{{ (data.priority or 'routine').upper() }}</div>
                    </div>
                    <div class="bg-white p-4 rounded shadow">
                        <div class="text-sm text-gray-500">Requested</div>
                        <div class="font-medium">{{ data.receivedAt }}</div>
                    </div>
                </div>
                {% for dept in data.perDepartment %}
                <div class="bg-white p-5 rounded shadow mb-6">
                    <div class="flex items-center justify-between mb-3">
                        <h2 class="text-lg font-semibold">{{ dept.department|title }}</h2>
                        <span class="text-sm px-2 py-1 rounded {{ 'bg-green-100 text-green-700' if dept.status=='completed' else 'bg-yellow-100 text-yellow-700' }}">{{ dept.status.replace('_',' ') }}</span>
                    </div>
                    {% if dept.results and dept.results|length > 0 %}
                        {% if dept.department == 'biochemistry' %}
                            <div class="overflow-x-auto">
                                <table class="min-w-full text-sm">
                                    <thead><tr class="text-left border-b"><th class="py-2 pr-4">Test</th><th class="py-2 pr-4">Value</th><th class="py-2 pr-4">Unit</th><th class="py-2 pr-4">Flag</th><th class="py-2">Ref Range</th></tr></thead>
                                    <tbody>
                                        {% for r in dept.results %}
                                        <tr class="border-b">
                                            <td class="py-2 pr-4">{{ r.testCode }}</td>
                                            <td class="py-2 pr-4">{{ r.value }}</td>
                                            <td class="py-2 pr-4">{{ r.unit }}</td>
                                            <td class="py-2 pr-4">{{ r.flag }}</td>
                                            <td class="py-2">{{ (r.referenceRange.low if r.referenceRange else '') }} - {{ (r.referenceRange.high if r.referenceRange else '') }}</td>
                                        </tr>
                                        {% endfor %}
                                    </tbody>
                                </table>
                            </div>
                            {% if dept.results[0].impression %}
                                <div class="mt-4"><div class="text-sm text-gray-600">Impression</div><div class="font-medium">{{ dept.results[0].impression }}</div></div>
                            {% endif %}
                        {% elif dept.department == 'microbiology' %}
                            {% set r = dept.results[0] %}
                            <div class="space-y-3">
                                <div><div class="text-sm text-gray-600">Findings</div><div class="whitespace-pre-wrap">{{ r.findings }}</div></div>
                                <div><div class="text-sm text-gray-600">Abnormal / Significant Findings</div><div class="whitespace-pre-wrap">{{ r.abnormalFindings }}</div></div>
                                <div><div class="text-sm text-gray-600">Impression</div><div class="whitespace-pre-wrap">{{ r.impression }}</div></div>
                            </div>
                        {% elif dept.department == 'pathology' %}
                            {% set r = dept.results[0] %}
                            <div class="space-y-3">
                                <div><div class="text-sm text-gray-600">Name of surgery</div><div class="whitespace-pre-wrap">{{ r.surgeryName }}</div></div>
                                <div><div class="text-sm text-gray-600">Nature of specimen</div><div class="whitespace-pre-wrap">{{ r.specimenNature }}</div></div>
                                <div><div class="text-sm text-gray-600">Intraoperative findings</div><div class="whitespace-pre-wrap">{{ r.intraoperativeFindings }}</div></div>
                                <div><div class="text-sm text-gray-600">Gross findings</div><div class="whitespace-pre-wrap">{{ r.grossFindings }}</div></div>
                                <div><div class="text-sm text-gray-600">Microscopic examination</div><div class="whitespace-pre-wrap">{{ r.microscopicExamination }}</div></div>
                                <div><div class="text-sm text-gray-600">Signature of the reporting doctor</div><div class="whitespace-pre-wrap">{{ r.reportingDoctor }}</div></div>
                            </div>
                        {% else %}
                            <pre class="text-xs bg-gray-50 p-3 rounded">{{ dept.results|tojson }}</pre>
                        {% endif %}
                    {% else %}
                        <div class="text-gray-500 text-sm">No results yet.</div>
                    {% endif %}
                </div>
                {% endfor %}
            </div>
        </body>
        </html>
        """, data=data)
    except Exception as e:
        return render_template_string("""<!DOCTYPE html><html><body><pre>{{e}}</pre></body></html>""", e=str(e))

@app.route("/download/<path:filename>")
def serve_report(filename):
    """Serves the downloaded test report from the 'downloads' directory."""
    # Check if user is logged in with a department
    if 'department' not in session:
        return redirect(url_for('test_login'))
    
    current_department = session['department']
    
    # Check if the order exists in our history and belongs to the current department
    hist = load_history()
    order_exists = False
    order_belongs_to_department = False
    
    for order in hist:
        if order.get("orderId") == filename:
            order_exists = True
            if order.get("department", "").lower() == current_department.lower():
                order_belongs_to_department = True
            break
    
    if not order_exists:
        error_page = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Error</title>
            <script src="https://cdn.tailwindcss.com"></script>
        </head>
        <body class="bg-gray-100 min-h-screen p-8">
            <div class="max-w-md mx-auto bg-white p-8 rounded-lg shadow-md">
                <h1 class="text-2xl font-bold text-red-600 mb-4">Error</h1>
                <p class="mb-4">Order ID {filename} not found in history.</p>
                <a href="/history" class="text-blue-600">Return to History</a>
            </div>
        </body>
        </html>
        """
        return error_page, 404
    
    if not order_belongs_to_department:
        error_page = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Access Denied</title>
            <script src="https://cdn.tailwindcss.com"></script>
        </head>
        <body class="bg-gray-100 min-h-screen p-8">
            <div class="max-w-md mx-auto bg-white p-8 rounded-lg shadow-md">
                <h1 class="text-2xl font-bold text-red-600 mb-4">Access Denied</h1>
                <p class="mb-4">You do not have permission to download this report.</p>
                <p class="mb-4">This report belongs to another department.</p>
                <a href="/history" class="text-blue-600">Return to History</a>
            </div>
        </body>
        </html>
        """
        return error_page, 403
    
    # Create a simple order details file for any order ID
    order_details = f"""Laboratory Test Order Details
==============================

Order ID: {filename}
Requested At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Status: Queued
Department: {current_department}

This order has been successfully submitted to the Laboratory Management System.
You can check the status using the "Check Status" button.

For any questions, please contact the laboratory department.
"""
    
    # Create a temporary file
    temp_file = os.path.join("downloads", f"order_{filename}.txt")
    with open(temp_file, 'w') as f:
        f.write(order_details)
    
    return send_from_directory("downloads", f"order_{filename}.txt", as_attachment=True)

@app.route("/history")
def history_page():
    # Check if department is selected
    if "department" not in session:
        return redirect(url_for("test_login"))
        
    # Get the current department from session
    department = session.get("department")
    
    # Load history filtered by department
    hist = load_history(department)
    # fetch status for each order (best-effort, non-blocking style)
    statuses = {}
    for h in hist[:20]:  # limit to last 20 for speed
        try:
            r = requests.get(f"{DEFAULT_HOST.rstrip('/')}/api/orders/{h['orderId']}", headers={'X-API-Key': SHARED_API_KEY}, timeout=5)
            if r.ok:
                j = r.json()
                per = j.get('perDepartment', [])
                any_completed = any(d.get('status') == 'completed' for d in per)
                statuses[h['orderId']] = 'completed' if any_completed else 'in_progress'
            else:
                statuses[h['orderId']] = 'unknown'
        except Exception:
            statuses[h['orderId']] = 'unknown'
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Order History</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://unpkg.com/feather-icons"></script>
    </head>
    <body class="bg-gray-100 min-h-screen">
        <div class="max-w-6xl mx-auto p-6">
            <div class="flex items-center justify-between mb-6">
                <div>
                    <h1 class="text-2xl font-bold">Order History</h1>
                    <p class="text-gray-600">Department: <span class="font-medium capitalize">{{ department }}</span></p>
                </div>
                <div class="space-x-2">
                    <a class="text-blue-600" href="/">New Request</a>
                    <a class="text-gray-600" href="/logout">Logout</a>
                </div>
            </div>
            <div class="bg-white rounded shadow overflow-hidden">
                <table class="min-w-full text-sm">
                    <thead class="bg-gray-50 text-gray-600">
                        <tr>
                            <th class="text-left px-4 py-2">Order ID</th>
                            <th class="text-left px-4 py-2">UHID</th>
                            <th class="text-left px-4 py-2">Dept</th>
                            <th class="text-left px-4 py-2">Priority</th>
                            <th class="text-left px-4 py-2">Created</th>
                            <th class="text-left px-4 py-2">Status</th>
                            <th class="text-left px-4 py-2">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for h in hist %}
                        <tr class="border-t">
                            <td class="px-4 py-2 font-medium">{{ h.orderId }}</td>
                            <td class="px-4 py-2">{{ h.uhid }}</td>
                            <td class="px-4 py-2 capitalize">{{ h.department }}</td>
                            <td class="px-4 py-2 uppercase">{{ h.priority }}</td>
                            <td class="px-4 py-2">{{ h.createdAt }}</td>
                            {% set st = statuses.get(h.orderId, 'unknown') %}
                            <td class="px-4 py-2">
                                <span class="px-2 py-1 rounded text-xs {{ 'bg-green-100 text-green-700' if st=='completed' else ('bg-yellow-100 text-yellow-700' if st=='in_progress' else 'bg-gray-100 text-gray-700') }}">{{ st.replace('_',' ') }}</span>
                            </td>
                            <td class="px-4 py-2 space-x-2">
                                <a class="inline-block bg-blue-600 text-white px-3 py-1 rounded" href="/results/{{ h.orderId }}">View Results</a>
                                <a class="inline-block bg-green-600 text-white px-3 py-1 rounded" href="/download/{{ h.orderId }}">View Report</a>
                                <a class="inline-block bg-gray-600 text-white px-3 py-1 rounded" href="/api/status/{{ h.orderId }}">Check Status</a>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% if not hist %}
                <div class="mt-6 text-gray-600">No orders submitted yet from this client.</div>
            {% endif %}
        </div>
        <script>feather.replace()</script>
    </body>
    </html>
    """, hist=hist, statuses=statuses, department=department)

@app.route("/api/health")
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'OK',
        'service': 'Laboratory Test Request System',
        'timestamp': datetime.now().isoformat(),
        'target_host': DEFAULT_HOST
    })
@app.route("/dicom/<path:filename>")
def serve_dicom(filename):
    """Serves the downloaded DICOM file from the 'downloads' directory."""
    # Serve as a raw binary stream so the WADO loader can parse it
    return send_from_directory("downloads", filename, mimetype="application/octet-stream")

@app.route('/analytics')
@login_required
def analytics():
    """Display analytics dashboard."""
    if session['user_role'] == 'nurse':
        flash("Nurse users do not have access to analytics.", "danger")
        return redirect(url_for('dashboard'))

    conn = get_db_connection()
    gender_data = {'Male': 0, 'Female': 0, 'Other': 0}
    age_distribution_data = {'0-18': 0, '19-35': 0, '36-55': 0, '56-75': 0, '75+': 0}
    monthly_case_trends_data = {}
    top_diagnoses_data = {}

    total_patients = 0
    total_medical_records = 0
    average_visits_per_patient = 0
    most_recent_record_date = "N/A"

    if conn:
        cursor = conn.cursor()
        try:
            # REMOVE or COMMENT OUT the problematic audit log insert
            # This was causing an error because 'uhid' is not defined in this context
            # cursor.execute(
            #     """INSERT INTO patient_edit_history (uhid, editor_id, field_name, old_value, new_value, edited_at)
            #        VALUES (%s, %s, %s, %s, %s, NOW())""",
            #     (uhid, session['user_id'], 'analytics_page_viewed', None, f"User {session['username']} viewed analytics dashboard.")
            # )
            # conn.commit()

            cursor.execute("SELECT COUNT(*) FROM patients")
            total_patients = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM patient_medical_records")
            total_medical_records = cursor.fetchone()[0]

            if total_patients > 0:
                cursor.execute("SELECT COUNT(DISTINCT uhid) FROM patient_medical_records")
                patients_with_records = cursor.fetchone()[0]
                if patients_with_records > 0:
                    cursor.execute("SELECT CAST(COUNT(uhid) AS DECIMAL) / COUNT(DISTINCT uhid) FROM patient_medical_records")
                    avg_visits_result = cursor.fetchone()[0]
                    if avg_visits_result is not None:
                        average_visits_per_patient = round(float(avg_visits_result), 2)
                else:
                    average_visits_per_patient = 0

            cursor.execute("SELECT MAX(visit_date) FROM patient_medical_records")
            recent_date_result = cursor.fetchone()[0]
            if recent_date_result:
                most_recent_record_date = recent_date_result.strftime('%Y-%m-%d')

            cursor.execute("SELECT gender, COUNT(*) FROM patients GROUP BY gender")
            for row in cursor.fetchall():
                gender = row[0] if row[0] else 'Unknown'
                count = row[1]
    
    # Normalize gender values to match your expected keys
                if gender and gender.strip():
                    gender_lower = gender.strip().lower()
                    if gender_lower == 'male' or gender_lower == 'm':
                        gender_data['Male'] += count
                    elif gender_lower == 'female' or gender_lower == 'f':
                        gender_data['Female'] += count
                    else:
                        gender_data['Other'] += count
                else:
                    gender_data['Other'] += count

            cursor.execute("SELECT dob FROM patients WHERE dob IS NOT NULL")
            dob_results = cursor.fetchall()
            today = datetime.now().date()
            for dob_row in dob_results:
                dob = dob_row[0]
                if dob:
                    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
                    if age <= 18:
                        age_distribution_data['0-18'] += 1
                    elif 19 <= age <= 35:
                        age_distribution_data['19-35'] += 1
                    elif 36 <= age <= 55:
                        age_distribution_data['36-55'] += 1
                    elif 56 <= age <= 75:
                        age_distribution_data['56-75'] += 1
                    else:
                        age_distribution_data['75+'] += 1

            cursor.execute("SELECT visit_date FROM patient_medical_records WHERE visit_date IS NOT NULL ORDER BY visit_date")
            trends_results = cursor.fetchall()
            month_year_counts = Counter()
            for row in trends_results:
                visit_date = row[0]
                if visit_date:
                    month_year = visit_date.strftime('%Y-%m')
                    month_year_counts[month_year] += 1
            monthly_case_trends_data = dict(sorted(month_year_counts.items()))

            cursor.execute("SELECT diagnosis FROM patient_medical_records WHERE diagnosis IS NOT NULL AND diagnosis != ''")
            diagnosis_results = cursor.fetchall()
            diagnoses_counter = Counter()
            for row in diagnosis_results:
                diagnosis = row[0].strip()
                if diagnosis:
                    diagnoses_counter[diagnosis] += 1
            top_diagnoses_data = dict(diagnoses_counter.most_common(10))

            print("=== ANALYTICS DATA DEBUG ===")
            print(f"Total patients: {total_patients}")
            print(f"Gender data: {gender_data}")
            print(f"Age distribution: {age_distribution_data}")
            print(f"Monthly trends: {monthly_case_trends_data}")
            print(f"Top diagnoses: {top_diagnoses_data}")

        except Exception as e:
            flash(f"Error fetching analytics data: {e}", "danger")
            print(f"Error fetching analytics data: {e}")
            import traceback
            traceback.print_exc()  # This will show the full error traceback
        finally:
            cursor.close()
            conn.close()

    return render_template('analytics.html',
                           total_patients=total_patients,
                           total_medical_records=total_medical_records,
                           average_visits_per_patient=f"{average_visits_per_patient:.2f}",
                           most_recent_record_date=most_recent_record_date,
                           gender_data=gender_data,
                           age_distribution_data=age_distribution_data,
                           monthly_case_trends_data=monthly_case_trends_data,
                           top_diagnoses_data=top_diagnoses_data)

@app.route('/dr_risk_assessment', methods=['POST'])
@login_required
def dr_risk_assessment():
    """API endpoint for Diabetic Retinopathy risk assessment (rule-based)."""
    if session['user_role'] == 'admin' or session['user_role'] == 'nurse':
        return jsonify({"error": "Access denied for this role."}), 403

    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        try:
            data = request.get_json()
            duration_diabetes_years = float(data.get('duration_diabetes_years', 0))
            hba1c = float(data.get('hba1c', 0))
            systolic_bp = float(data.get('systolic_bp', 0))
            diastolic_bp = float(data.get('diastolic_bp', 0))
            has_kidney_disease = data.get('has_kidney_disease', False)
            has_high_cholesterol = data.get('has_high_cholesterol', False)

            risk_score = 0
            risk_category = "No Diabetic Retinopathy (No DR detected)"
            risk_implication = "Annual screening recommended."

            if duration_diabetes_years > 10: risk_score += 3
            elif duration_diabetes_years > 5: risk_score += 1

            if hba1c >= 8.0: risk_score += 4
            elif hba1c >= 7.0: risk_score += 2

            if systolic_bp >= 140 or diastolic_bp >= 90: risk_score += 2
            if has_kidney_disease: risk_score += 3
            if has_high_cholesterol: risk_score += 1

            if risk_score >= 10:
                risk_category = "Proliferative Diabetic Retinopathy (PDR)"
                risk_implication = "Immediate ophthalmology referral for laser or surgical intervention required."
            elif risk_score >= 7:
                risk_category = "Severe Non-Proliferative Diabetic Retinopathy (Severe NPDR)"
                risk_implication = "Urgent ophthalmology referral for potential treatment to prevent vision loss."
            elif risk_score >= 4:
                risk_category = "Moderate Non-Proliferative Diabetic Retinopathy (Moderate NPDR)"
                risk_implication = "Regular follow-ups (e.g., 4-6 months) and intensive diabetes/BP management crucial."
            elif risk_score >= 2:
                risk_category = "Mild Non-Proliferative Diabetic Retinopathy (Mild NPDR)"
                risk_implication = "Close monitoring (e.e., 6-12 months) and strict diabetes control advised."

            cursor.execute(
                """INSERT INTO patient_edit_history (patient_id, editor_id, field_name, old_value, new_value, edited_at)
                   VALUES (%s, %s, %s, %s, %s, NOW())""",
                (None, session['user_id'], 'dr_risk_assessment_performed', None, f"Risk Category: {risk_category}, Score: {risk_score}, Implication: {risk_implication}")
            )
            conn.commit()
            cursor.close()
            conn.close()

            return jsonify({
                "risk_score": risk_score,
                "risk_category": risk_category,
                "implication": risk_implication,
                "message": "This is a simplified rule-based assessment and not a medical diagnosis."
            })
        except Exception as e:
            if conn: conn.rollback()
            return jsonify({"error": str(e)}), 400
        finally:
            if conn and not cursor.closed:
                cursor.close()
            if conn and not conn.closed:
                conn.close()
    else:
        return jsonify({"error": "Database connection failed."}), 500
    
@app.route('/prescription/<uhid>', methods=['GET', 'POST'])
@login_required
def prescription_page(uhid):
    """
    Handles displaying the new prescription form (GET) and saving 
    the prescription data to the database (POST).
    Note: Now fetches patient data by MRN since UHID is no longer in the route path.
    """
    
    conn = None
    cursor = None
    patient = None # Initialize patient outside of try block
    patient_uhid = None # Initialize UHID fallback

    try:
        conn = get_db_connection()
        if not conn:
            flash("Database connection error.", 'danger')
            return redirect(url_for('dashboard'))

        # --- STEP 1: Fetch Patient Data (using MRN only) ---
        # We need to manually fetch the patient since the original helper relied on UHID.
        # This assumes DictCursor is used for easy dictionary access.
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Assuming MRN is sufficient to look up the primary patient record
        cursor.execute("SELECT * FROM patients WHERE uhid = %s", (uhid,))
        patient_row = cursor.fetchone()
        
        if patient_row:
            patient = dict(patient_row)
            # Safely get the UHID, which is needed for the redirect later
            patient_uhid = patient.get('uhid')
            if not patient_uhid:
                 patient_uhid = "N/A" # Fallback if UHID field is empty
        else:
            flash(f'Patient with uhid {uhid} not found.', 'danger')
            return redirect(url_for('dashboard'))
        
        # Close cursor for read operation before starting potential write operation
        cursor.close() 

        # --- STEP 2: Process POST Request (Saving Prescription) ---
        if request.method == 'POST':
            # Re-open cursor for write operation
            cursor = conn.cursor() 
            visit_date = request.form['visit_date']
            
            form_data = request.form
            
            # --- Extract Spectacle/Refraction Data ---
            spectacle_data = {
                'od_sph': form_data.get('spectacle_od_sph'),
                'od_cyl': form_data.get('spectacle_od_cyl'),
                'od_axis': form_data.get('spectacle_od_axis'),
                'od_add': form_data.get('spectacle_od_add'),
                'od_prism': form_data.get('spectacle_od_prism'),
                'od_va': form_data.get('spectacle_od_va'),
                'os_sph': form_data.get('spectacle_os_sph'),
                'os_cyl': form_data.get('spectacle_os_cyl'),
                'os_axis': form_data.get('spectacle_os_axis'),
                'os_add': form_data.get('spectacle_os_add'),
                'os_prism': form_data.get('spectacle_os_prism'),
                'os_va': form_data.get('spectacle_os_va'),
            }
            lens_type = form_data.get('lens_type')

            # --- Extract Dynamic Medication Data ---
            medications = []
            med_index = 1
            while True:
                name_key = f'medication_name_{med_index}'
                
                # Check if the next medication block exists
                if name_key not in form_data:
                    break
                
                # Only save if the drug name is provided and not empty
                if form_data.get(name_key, '').strip():
                    medication = {
                        'name': form_data.get(name_key).strip(),
                        'dose': form_data.get(f'medication_dose_{med_index}'),
                        'frequency': form_data.get(f'medication_frequency_{med_index}'),
                        'eye': form_data.get(f'medication_eye_{med_index}'),
                        'duration_value': form_data.get(f'medication_duration_value_{med_index}'),
                        'duration_unit': form_data.get(f'medication_duration_unit_{med_index}'),
                    }
                    medications.append(medication)
                
                med_index += 1
            
            # --- Extract Notes and Follow-up ---
            systemic_medication = form_data.get('systemic_medication')
            surgery_recommendation = form_data.get('surgery_recommendation')
            iol_notes = form_data.get('iol_notes')
            patient_instructions = form_data.get('patient_instructions')
            
            follow_up_date_str = form_data.get('follow_up_date')
            follow_up_date = follow_up_date_str if follow_up_date_str else None

            
            # --- Save to Database ---
            
            # IMPORTANT: session['user_id'] must hold the integer ID of the logged-in user
            user_id = session.get('user_id', 1) 

            insert_query = """
                INSERT INTO patient_prescriptions (
                    uhid, created_by, spectacle_lens, lens_type, medications, 
                    systemic_medication, surgery_recommendation, iol_notes, 
                    patient_instructions, follow_up_date, visit_date
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(insert_query, ( 
                uhid, 
                user_id,
                json.dumps(spectacle_data), 
                lens_type, 
                json.dumps(medications),    
                systemic_medication, 
                surgery_recommendation, 
                iol_notes, 
                patient_instructions, 
                follow_up_date,
                visit_date
            ))
            
            conn.commit()
            flash('Prescription saved successfully!', 'success')
            
            # SUCCESS REDIRECT: Go back to the patient's main view using MRN and the retrieved UHID
        
        # --- STEP 3: Handle GET Request (Displaying the Form) ---
        return render_template('prescription_form.html', patient=patient)
    
    except Exception as e:
        error_message = f"ERROR: Prescription processing failed. Details: {e}"
        print(f"FATAL SERVER ERROR in prescription_page: {error_message}") 
        flash(error_message, 'danger')
        
        if conn: conn.rollback()
        
        # ERROR FALLBACK REDIRECT
        # Use the retrieved patient_uhid if available, otherwise just redirect to dashboard
        if patient_uhid and uhid:
            return redirect(url_for('view_medical_history', uhid=uhid))
        else:
            return redirect(url_for('dashboard')) 

    finally:
        # Cleanup connection resources
        if cursor and not cursor.closed: cursor.close()
        if conn: conn.close()    


if __name__ == '__main__':
    # Imports for database functions (ensure database.py is in the same directory)
    from database import create_tables, ensure_columns
    
    with app.app_context():
        # 🚨 This is the key startup sequence:
        create_tables() # Ensure all base tables exist
        ensure_columns() # Ensure the new 'prescription_details' column exists in patient_medical_records
    
    app.run(debug=True)