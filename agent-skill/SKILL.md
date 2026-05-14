---
name: job-application-agent
description: Execute the autonomous job application pipeline. Accepts a job URL, resume file, and optional cover letter, and fills out ATS forms. Uses the backend server to apply and requires user confirmation to submit.
---

# Auto Job Applier Skill

You are equipped to autonomously apply to jobs on behalf of the user using the OpenDoor `apply-agent` system.

## Location
Assume the user has the `apply-agent` codebase downloaded. Unless specified otherwise, execute these commands from within the root of the `apply-agent` repository.

## Workflow

When the user asks you to apply to a job with a resume and cover letter:

1. **Initialization & Profile Verification**:
   - Check if `profile.yaml` exists. If not, inform the user and offer to run the setup (reading their resume).
   - Check if the backend server is running on port 8000. If it isn't, start it by navigating to the codebase directory and running:
   `python app.py` (in a persistent terminal).
   - **MANDATORY**: Fetch the current profile via `GET /profile`. Summarize the key values (Salary, Notice Period, Work Authorization, EEO) for the user.
   - **ASK**: "Should I use these default values, or should I override anything (salary, sponsorship, etc.) for this application?"

2. **Trigger the Application Pipeline**:
   - You should use Python (via a temporary script or inline execution) to send a `multipart/form-data` POST request to `http://localhost:8000/run`.
   - Pass the job URL as `job_url`.
   - Upload the provided resume file as `resume`.
   - Upload the provided cover letter as `cover_letter` (if provided).
   - Include any override fields the user requested (e.g., `desired_salary`, `notice_period`, `authorized_canada`, `requires_sponsorship`, `gender`, `race_ethnicity`, `veteran_status`, `disability_status`).
   - The response will give you a `session_id`.

3. **Monitor Progress**:
   - Read the Server-Sent Events (SSE) from `http://localhost:8000/stream/{session_id}`. You can write a small Python script to listen to the SSE and stream the logs into your context, or simply inform the user the agent is running and wait for it to finish. 
   - When the stream finishes (it returns a `{"type": "done", "result": {...}}` JSON payload), it means the application form has been filled and the agent is paused on the final review screen.

4. **Human-in-the-Loop Review**:
   - Parse the `result` from the SSE stream. Present the following to the user in the chat:
   - The feedback on how the resume was tailored.
   - The table of filled fields.
   - Ask the user: "Everything is filled. Do you want me to submit the application, or would you like to edit a field or cancel?"

5. **Final Execution**:
   - If the user says **Submit**: Send a POST request to `http://localhost:8000/submit`. Tell the user it has been submitted and a confirmation screenshot is saved in `output/confirmation.png`.
   - If the user wants to **Edit** a field: Send a POST request to `http://localhost:8000/edit_field` with `label` and `new_value`.
   - If the user says **Cancel**: Send a POST request to `http://localhost:8000/cancel`.
