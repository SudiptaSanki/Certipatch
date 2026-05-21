// CertiPatch v3 - frontend logic
const API_BASE = "";

// --- State ---
let currentFile = null;
let isEngineRunning = false;

// --- Init ---
window.onload = async () => {
    await resetTransientSession();
    await fetchAccounts();
    await fetchSettings();
    loadPdfList();
    updateStatus();
    setInterval(updateStatus, 3000);
};

// ============================================================
// STEP 1: ACCOUNTS
// ============================================================
async function fetchAccounts() {
    try {
        const res = await fetch(`${API_BASE}/api/accounts`);
        const accounts = await res.json();
        const list = document.getElementById('accountList');
        if (!accounts.length) {
            list.innerHTML = '<p style="color: var(--text-secondary); font-size: 13px; margin-top: 10px;">No accounts added yet.</p>';
            return;
        }
        list.innerHTML = accounts.map(acc => `
            <div class="account-item">
                <div class="account-info">
                    <span class="account-email">${escapeHtml(acc.email)}</span>
                    <span class="account-stats">${acc.daily_sent}/${acc.daily_limit || 500} sent today</span>
                </div>
                <button onclick="removeAccount(${acc.id})" style="color: var(--error-color); background: none; border: none; cursor: pointer; font-size: 13px;">Remove</button>
            </div>
        `).join('');
    } catch (e) {
        console.error('fetchAccounts failed:', e);
    }
}

async function addAccount() {
    const email = document.getElementById('accEmail').value.trim();
    const password = document.getElementById('accPass').value.trim();

    if (!email || !password) {
        alert("Please fill in both the Gmail address and the App Password.");
        return;
    }

    const cleanPass = password.replace(/\s/g, '');
    if (cleanPass.length !== 16) {
        alert("App Passwords are exactly 16 characters. You entered " + cleanPass.length + ". Check you copied it correctly.");
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/accounts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password: cleanPass })
        });
        const data = await res.json();

        if (res.ok) {
            document.getElementById('accEmail').value = '';
            document.getElementById('accPass').value = '';
            fetchAccounts();
            alert('Account added: ' + email);
        } else {
            alert('Error: ' + (data.detail || 'Could not add account.'));
        }
    } catch (e) {
        alert('Could not connect to server. Make sure Run.bat is running.');
        console.error(e);
    }
}

async function removeAccount(id) {
    if (!confirm("Remove this account?")) return;
    await fetch(`${API_BASE}/api/accounts/${id}`, { method: 'DELETE' });
    fetchAccounts();
}

// ============================================================
// STEP 2: SETTINGS
// ============================================================
async function fetchSettings() {
    try {
        const res = await fetch(`${API_BASE}/api/settings`);
        const data = await res.json();
        document.getElementById('emailSubject').value = data.subject;
        document.getElementById('emailBody').value = data.body_template;
    } catch (e) {
        console.error('fetchSettings failed:', e);
    }
}

async function saveSettings() {
    const subject = document.getElementById('emailSubject').value;
    const body_template = document.getElementById('emailBody').value;
    await fetch(`${API_BASE}/api/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subject, body_template })
    });
    alert("Template saved.");
}

// ============================================================
// STEP 3: PDF FOLDER, FILE UPLOAD, QUEUE, ENGINE
// ============================================================

async function resetTransientSession() {
    try {
        await fetch(`${API_BASE}/api/session/reset`, { method: 'POST' });
        currentFile = null;
    } catch (e) {
        console.error('resetTransientSession failed:', e);
    }
}

async function loadPdfList() {
    try {
        const res = await fetch(`${API_BASE}/api/upload/certificates`);
        const data = await res.json();
        const listEl = document.getElementById('pdfFileList');
        if (data.files && data.files.length > 0) {
            listEl.innerHTML = `
                <div style="margin-bottom: 8px; color: var(--text-primary);">${data.files.length} PDF file(s) ready</div>
                ${data.files.map(f => `<div style="margin-bottom: 4px;">PDF: ${escapeHtml(f)}</div>`).join('')}
            `;
        } else {
            listEl.innerHTML = 'No PDF folder selected yet.';
        }
    } catch (e) {
        console.error('Failed to load PDF list', e);
    }
}

async function uploadCertificateFiles(files, replaceExisting) {
    if (!files || files.length === 0) return;

    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
        formData.append('files', files[i]);
    }
    formData.append('replace', replaceExisting ? 'true' : 'false');

    try {
        const res = await fetch(`${API_BASE}/api/upload/certificates`, {
            method: 'POST',
            body: formData
        });
        const data = await res.json();

        if (res.ok && data.count > 0) {
            alert(`${data.count} PDF file(s) uploaded successfully.`);
            loadPdfList();
        } else if (res.ok) {
            alert('No PDF files were found in that selection.');
        } else {
            alert('Error uploading PDFs: ' + (data.detail || 'Could not upload files.'));
        }
    } catch (e) {
        alert('Could not connect to server.');
    }
}

function uploadPdfFolder(files) {
    uploadCertificateFiles(files, true);
}

function uploadMorePdfs(files) {
    uploadCertificateFiles(files, false);
}

async function handleFileUpload(file) {
    if (!file) return;
    currentFile = file;

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await fetch(`${API_BASE}/api/upload/preview`, {
            method: 'POST',
            body: formData
        });
        const data = await res.json();

        if (res.ok) {
            document.getElementById('columnMapping').style.display = 'block';
            populateSelect('colName', data.headers, data.detected.name_col);
            populateSelect('colEmail', data.headers, data.detected.email_col);
            populateFileSelect('colFile', data.headers, data.detected.file_col);

            const rowCount = data.preview.length;
            document.getElementById('dropzone').innerHTML = `
                <p style="color: var(--accent-secondary);">${escapeHtml(file.name)} loaded (${rowCount}+ preview rows)</p>
                <button onclick="resetFileUpload(event)" style="margin-top:8px; color: var(--error-color); background: none; border: 1px solid var(--error-color); border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 12px;">Remove File</button>
            `;
        } else {
            alert(formatApiError(data, "Could not read file."));
        }
    } catch (e) {
        alert("Could not connect to server.");
        console.error(e);
    }
}

function resetFileUpload(e) {
    if (e) e.stopPropagation();
    currentFile = null;
    document.getElementById('columnMapping').style.display = 'none';
    document.getElementById('dropzone').innerHTML = `
        <svg style="width: 40px; margin-bottom: 10px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="17 8 12 3 7 8"></polyline>
            <line x1="12" y1="3" x2="12" y2="15"></line>
        </svg>
        <p>Drop your Excel or CSV list here</p>
        <span style="font-size: 12px; color: var(--text-secondary);">System will auto-detect Name, Email, and PDF columns</span>
        <input type="file" id="fileInput" hidden onchange="handleFileUpload(this.files[0])">
    `;
}

function populateSelect(id, headers, detected) {
    const sel = document.getElementById(id);
    sel.innerHTML = headers.map(h =>
        `<option value="${escapeHtml(h)}" ${h === detected ? 'selected' : ''}>${escapeHtml(h)}</option>`
    ).join('');
}

function populateFileSelect(id, headers, detected) {
    const sel = document.getElementById(id);
    const options = [`<option value="" ${!detected ? 'selected' : ''}>Match PDFs from Name column</option>`];
    options.push(...headers.map(h =>
        `<option value="${escapeHtml(h)}" ${h === detected ? 'selected' : ''}>${escapeHtml(h)}</option>`
    ));
    sel.innerHTML = options.join('');
}

async function loadAndSend() {
    if (!currentFile) {
        alert("Please upload a CSV or Excel file first.");
        return;
    }

    const emCol = document.getElementById('colEmail').value;
    if (!emCol) {
        alert("Please select the Email column.");
        return;
    }

    const formData = new FormData();
    formData.append('file', currentFile);
    formData.append('name_col', document.getElementById('colName').value || 'Name');
    formData.append('email_col', emCol);
    formData.append('file_col', document.getElementById('colFile').value || '');
    formData.append('replace_queue', 'true');

    try {
        const res = await fetch(`${API_BASE}/api/upload/load`, {
            method: 'POST',
            body: formData
        });
        const data = await res.json();

        if (res.ok) {
            let message = `${data.added} recipients loaded into a fresh queue.\n${data.skipped} skipped as duplicates inside this file.\n${data.matched_pdfs} PDF match(es) verified.\n\nTotal in queue: ${data.total_in_queue}`;
            if (data.unused_pdfs && data.unused_pdfs.length) {
                message += `\n\nNote: ${data.unused_pdfs.length} PDF file(s) were not used by the sheet.`;
            }
            alert(message);
            document.getElementById('columnMapping').style.display = 'none';
            updateStatus();
        } else {
            alert(formatApiError(data, "Could not load file."));
        }
    } catch (e) {
        alert("Could not connect to server.");
        console.error(e);
    }
}

// ============================================================
// ENGINE CONTROLS
// ============================================================
async function toggleEngine() {
    const endpoint = isEngineRunning ? 'pause' : 'start';
    try {
        const res = await fetch(`${API_BASE}/api/engine/${endpoint}`, { method: 'POST' });
        const data = await res.json();
        alert(data.message);
        updateStatus();
    } catch (e) {
        alert("Could not connect to server.");
    }
}

async function clearQueue() {
    if (!confirm("This will remove ALL jobs (pending, sent, failed) from the queue. Continue?")) return;
    try {
        await fetch(`${API_BASE}/api/jobs`, { method: 'DELETE' });
        alert("Queue cleared.");
        updateStatus();
    } catch (e) {
        alert("Error clearing queue.");
    }
}

async function clearCompleted() {
    if (!confirm("Remove all Sent and Failed jobs? Pending jobs will be kept.")) return;
    try {
        const res = await fetch(`${API_BASE}/api/jobs/clear-completed`, { method: 'POST' });
        const data = await res.json();
        alert(data.message || 'Done.');
        updateStatus();
    } catch (e) {
        alert("Error clearing completed jobs.");
    }
}

async function sendTest() {
    try {
        const res = await fetch(`${API_BASE}/api/accounts`);
        const accounts = await res.json();
        if (accounts.length === 0) {
            alert("Please add at least one sender account in Step 1 first.");
            return;
        }

        const testEmail = prompt("Enter YOUR email address to receive the test certificate:");
        if (!testEmail) return;

        alert("Sending test email... This may take a few seconds.");
        const testRes = await fetch(`${API_BASE}/api/test-send`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ to_email: testEmail, account_id: accounts[0].id })
        });
        const result = await testRes.json();
        if (result.success) {
            alert("Test email sent. Check your inbox at: " + testEmail);
        } else {
            alert("Failed: " + result.message);
        }
    } catch (e) {
        alert("Could not connect to server.");
    }
}

// ============================================================
// STATUS POLLING
// ============================================================
async function updateStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/engine/status`);
        const data = await res.json();

        isEngineRunning = data.is_sending;

        const statusEl = document.getElementById('engineStatus');
        if (isEngineRunning) {
            statusEl.innerText = `Sending... (${data.pending} left)`;
            statusEl.style.color = "var(--success-color)";
        } else if (data.total > 0 && data.pending === 0) {
            statusEl.innerText = "Complete";
            statusEl.style.color = "var(--accent-secondary)";
        } else {
            statusEl.innerText = "Engine Idle";
            statusEl.style.color = "var(--text-secondary)";
        }

        const startBtn = document.getElementById('startBtn');
        startBtn.innerText = isEngineRunning ? "Pause Engine" : "Launch Bulk Send";
        startBtn.style.background = isEngineRunning ? "#f59e0b" : "var(--success-color)";

        document.getElementById('countPending').innerText = data.pending;
        document.getElementById('countSent').innerText = data.sent;
        document.getElementById('countFailed').innerText = data.failed;
    } catch (e) {
        // Server probably not running yet.
    }

    // Fetch quota data for the quota card
    try {
        const qRes = await fetch(`${API_BASE}/api/jobs/quota`);
        const quota = await qRes.json();
        const quotaEl = document.getElementById('countQuota');
        const detailEl = document.getElementById('quotaDetail');
        if (quotaEl) {
            quotaEl.innerText = quota.remaining != null ? quota.remaining : '\u2014';
        }
        if (detailEl) {
            detailEl.innerText = `${quota.used_in_window || 0}/${quota.total || 0} used \u00B7 ${quota.accounts || 0} account(s) \u00B7 ${quota.window_hours || 24}h window`;
        }
    } catch (e) {
        // quota endpoint not available yet
    }
}

// ============================================================
// UI HELPERS
// ============================================================
function showGuide() { document.getElementById('guideModal').style.display = 'flex'; }
function hideGuide() { document.getElementById('guideModal').style.display = 'none'; }

function downloadSample(e) {
    e.preventDefault();
    const csv = "Name,Email,Certificate_File\nTest User,your@email.com,test_cert.pdf\nJane Doe,jane@example.com,cert_1.pdf";
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.setAttribute('hidden', '');
    a.setAttribute('href', url);
    a.setAttribute('download', 'CertiPatch_Sample.csv');
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[ch]));
}

function formatApiError(data, fallback) {
    const detail = data && data.detail;
    if (!detail) return "Error: " + fallback;
    if (typeof detail === 'string') return "Error: " + detail;

    const lines = [detail.message || fallback];
    const unmatched = detail.unmatched || {};

    if (unmatched.missing_pdf && unmatched.missing_pdf.length) {
        lines.push("");
        lines.push("Missing PDFs:");
        unmatched.missing_pdf.slice(0, 10).forEach(item => {
            lines.push(`Row ${item.row}: expected "${item.expected || item.name || item.email}"`);
        });
        if (unmatched.missing_pdf.length > 10) {
            lines.push(`...and ${unmatched.missing_pdf.length - 10} more.`);
        }
    }

    if (unmatched.missing_email && unmatched.missing_email.length) {
        lines.push("");
        lines.push(`${unmatched.missing_email.length} row(s) are missing an email address.`);
    }

    if (unmatched.available_pdfs && unmatched.available_pdfs.length) {
        lines.push("");
        lines.push(`${unmatched.available_pdfs.length} PDF file(s) are currently available.`);
    }

    return lines.join("\n");
}
