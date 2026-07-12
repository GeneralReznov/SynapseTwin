from __future__ import annotations
import os
import json
import base64
import logging
import httpx

logger = logging.getLogger(__name__)

GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL    = "llama-3.3-70b-versatile"   # Best Groq model for complex tasks
GROQ_FAST     = "llama-3.1-8b-instant"      # Fast model for simple tasks


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }


def is_available() -> bool:
    return bool(GROQ_API_KEY)


# ── Speech-to-Text (fallback for Sarvam STT) ────────────────────────────────────

_EXT_MAP: dict[str, str] = {
    "audio/webm": "webm", "audio/mp4": "mp4", "audio/ogg": "ogg",
    "audio/wav": "wav", "audio/mpeg": "mp3", "audio/x-m4a": "m4a",
}


async def speech_to_text(audio_bytes: bytes, mime_type: str = "audio/webm", language_code: str | None = None) -> dict:
    """STT via Groq's Whisper endpoint — primary speech-to-text provider."""
    if not GROQ_API_KEY:
        return {"success": False, "transcript": "", "error": "GROQ_API_KEY not configured"}

    base_mime = mime_type.split(";")[0].strip().lower()
    ext = _EXT_MAP.get(base_mime, "webm")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            files = {"file": (f"audio.{ext}", audio_bytes, base_mime or "audio/webm")}
            data: dict = {"model": "whisper-large-v3"}
            if language_code:
                data["language"] = language_code.split("-")[0]  # Groq wants ISO-639-1, e.g. "hi"
            resp = await client.post(
                f"{GROQ_BASE_URL}/audio/transcriptions",
                files=files, data=data,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            )
            resp.raise_for_status()
            return {"success": True, "transcript": resp.json().get("text", ""), "provider": "groq"}
    except Exception as exc:
        logger.error(f"Groq STT error: {exc}")
        return {"success": False, "transcript": "", "error": str(exc)}


# ── Text-to-Speech (fallback for Sarvam TTS) ────────────────────────────────────

async def text_to_speech(text: str, voice: str = "Aaliyah-PlayAI") -> dict:
    """TTS via Groq's PlayAI model — primary text-to-speech provider."""
    if not GROQ_API_KEY:
        return {"success": False, "error": "GROQ_API_KEY not configured"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{GROQ_BASE_URL}/audio/speech",
                json={
                    "model":           "playai-tts",
                    "input":           text[:2000],
                    "voice":           voice,
                    "response_format": "wav",
                },
                headers=_headers(),
            )
            resp.raise_for_status()
            audio_b64 = base64.b64encode(resp.content).decode("utf-8")
            return {"success": True, "audioBase64": audio_b64, "provider": "groq", "format": "wav"}
    except Exception as exc:
        logger.error(f"Groq TTS error: {exc}")
        return {"success": False, "error": str(exc)}


async def chat_completion(
    system_prompt: str,
    user_message: str,
    json_mode: bool = False,
    fast: bool = False,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> dict:
    """Call Groq LLM. Falls back gracefully if key not configured."""
    if not GROQ_API_KEY:
        return {"success": False, "content": "", "error": "GROQ_API_KEY not configured"}

    model = GROQ_FAST if fast else GROQ_MODEL
    payload: dict = {
        "model":    model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last_exc = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                resp = await client.post(
                    f"{GROQ_BASE_URL}/chat/completions",
                    json=payload,
                    headers=_headers(),
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return {"success": True, "content": content, "model": model}
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                import asyncio
                await asyncio.sleep(1.5 * (attempt + 1))

    logger.error(f"Groq LLM error after 3 attempts: {last_exc}")
    return {"success": False, "content": "", "error": str(last_exc)}


async def analyze_json(system_prompt: str, user_message: str) -> dict:
    """Convenience wrapper that always requests JSON output."""
    result = await chat_completion(system_prompt, user_message, json_mode=True)
    if not result["success"]:
        return {}
    try:
        import re
        text = result["content"].strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text.strip())
    except Exception as e:
        logger.error(f"Groq JSON parse error: {e} — content: {result['content'][:200]}")
        return {}


async def generate_course_recommendations(
    user_name: str,
    user_role: str,
    job_title: str,
    department: str,
    recent_topics: list[str],
    platform: str = "both",
) -> dict:
    """Generate role-based course recommendations for Coursera and Udemy."""

    COURSE_DB = {
        "engineering": {
            "coursera": [
                {"title": "Machine Learning Specialization", "instructor": "Andrew Ng", "rating": 4.9, "students": "4.2M", "duration": "3 months", "level": "Intermediate", "url": "https://www.coursera.org/specializations/machine-learning-introduction", "skills": ["ML", "Python", "Neural Networks"]},
                {"title": "Google Cloud Professional DevOps Engineer", "instructor": "Google Cloud", "rating": 4.8, "students": "892K", "duration": "6 months", "level": "Advanced", "url": "https://www.coursera.org/professional-certificates/sre-devops-engineer-google-cloud", "skills": ["DevOps", "GCP", "SRE", "CI/CD"]},
                {"title": "IBM Full Stack Software Developer", "instructor": "IBM", "rating": 4.6, "students": "180K", "duration": "4 months", "level": "Beginner", "url": "https://www.coursera.org/professional-certificates/ibm-full-stack-cloud-developer", "skills": ["React", "Node.js", "Docker", "Kubernetes"]},
                {"title": "Deep Learning Specialization", "instructor": "deeplearning.ai", "rating": 4.9, "students": "1.2M", "duration": "5 months", "level": "Intermediate", "url": "https://www.coursera.org/specializations/deep-learning", "skills": ["Deep Learning", "TensorFlow", "NLP"]},
                {"title": "Software Engineering: Introduction", "instructor": "UBC", "rating": 4.7, "students": "120K", "duration": "6 weeks", "level": "Beginner", "url": "https://www.coursera.org/learn/software-engineering-introduction", "skills": ["Software Design", "Testing", "Agile"]},
                {"title": "System Design Interview", "instructor": "Exponent", "rating": 4.8, "students": "95K", "duration": "8 weeks", "level": "Advanced", "url": "https://www.coursera.org/learn/system-design-interview", "skills": ["System Design", "Scalability", "Architecture"]},
            ],
            "udemy": [
                {"title": "The Complete Python Bootcamp From Zero to Hero", "instructor": "Jose Portilla", "rating": 4.6, "students": "1.8M", "duration": "22 hours", "level": "Beginner", "url": "https://www.udemy.com/course/complete-python-bootcamp/", "skills": ["Python", "OOP", "Data Structures"]},
                {"title": "AWS Certified Solutions Architect – Associate", "instructor": "Stephane Maarek", "rating": 4.7, "students": "960K", "duration": "27 hours", "level": "Intermediate", "url": "https://www.udemy.com/course/aws-certified-solutions-architect-associate-saa-c03/", "skills": ["AWS", "Cloud", "Architecture"]},
                {"title": "Docker & Kubernetes: The Practical Guide", "instructor": "Maximilian Schwarzmüller", "rating": 4.7, "students": "220K", "duration": "24 hours", "level": "Intermediate", "url": "https://www.udemy.com/course/docker-kubernetes-the-practical-guide/", "skills": ["Docker", "Kubernetes", "DevOps"]},
                {"title": "React - The Complete Guide", "instructor": "Maximilian Schwarzmüller", "rating": 4.6, "students": "980K", "duration": "68 hours", "level": "Intermediate", "url": "https://www.udemy.com/course/react-the-complete-guide-incl-redux/", "skills": ["React", "Redux", "Hooks"]},
                {"title": "System Design for Beginners to Advanced", "instructor": "Gaurav Sen", "rating": 4.8, "students": "85K", "duration": "18 hours", "level": "Advanced", "url": "https://www.udemy.com/course/system-design-a-comprehensive-guide/", "skills": ["System Design", "Microservices", "Caching"]},
                {"title": "Complete Machine Learning & Data Science Bootcamp", "instructor": "Andrei Neagoie", "rating": 4.6, "students": "310K", "duration": "47 hours", "level": "Intermediate", "url": "https://www.udemy.com/course/complete-machine-learning-and-data-science-zero-to-mastery/", "skills": ["ML", "Data Science", "Pandas"]},
            ],
        },
        "management": {
            "coursera": [
                {"title": "Google Project Management Certificate", "instructor": "Google", "rating": 4.8, "students": "2.1M", "duration": "6 months", "level": "Beginner", "url": "https://www.coursera.org/professional-certificates/google-project-management", "skills": ["Project Management", "Agile", "Scrum"]},
                {"title": "Leadership and Management Specialization", "instructor": "University of Michigan", "rating": 4.8, "students": "450K", "duration": "5 months", "level": "Intermediate", "url": "https://www.coursera.org/specializations/leadership-development-for-engineers", "skills": ["Leadership", "Strategy", "People Management"]},
                {"title": "Executive Leadership", "instructor": "Columbia University", "rating": 4.7, "students": "89K", "duration": "3 months", "level": "Advanced", "url": "https://www.coursera.org/learn/executive-leadership", "skills": ["Executive Leadership", "Strategy", "Influence"]},
                {"title": "Inspiring and Motivating Individuals", "instructor": "Michigan", "rating": 4.7, "students": "340K", "duration": "4 weeks", "level": "Intermediate", "url": "https://www.coursera.org/learn/inspiring-motivating-individuals", "skills": ["Motivation", "Team Building", "Culture"]},
                {"title": "Managing Talent", "instructor": "Michigan", "rating": 4.6, "students": "210K", "duration": "4 weeks", "level": "Intermediate", "url": "https://www.coursera.org/learn/managing-talent", "skills": ["Talent Management", "Performance Reviews", "Coaching"]},
                {"title": "Successful Negotiation: Essential Strategies", "instructor": "University of Michigan", "rating": 4.8, "students": "890K", "duration": "7 weeks", "level": "Beginner", "url": "https://www.coursera.org/learn/negotiation-skills", "skills": ["Negotiation", "Conflict Resolution", "Persuasion"]},
            ],
            "udemy": [
                {"title": "Engineering Leadership Bootcamp", "instructor": "Erhan Bas", "rating": 4.7, "students": "42K", "duration": "12 hours", "level": "Intermediate", "url": "https://www.udemy.com/course/engineering-manager/", "skills": ["Engineering Management", "1:1s", "Career Growth"]},
                {"title": "The Complete Management Skills Certification Course", "instructor": "Chris Croft", "rating": 4.5, "students": "78K", "duration": "21 hours", "level": "Beginner", "url": "https://www.udemy.com/course/management-skills-course/", "skills": ["Management", "Delegation", "Feedback"]},
                {"title": "Agile Project Management: Scrum Step by Step", "instructor": "Joseph Phillips", "rating": 4.6, "students": "150K", "duration": "8 hours", "level": "Beginner", "url": "https://www.udemy.com/course/agile-project-management/", "skills": ["Agile", "Scrum", "Sprints"]},
                {"title": "OKR Goal Setting: The Complete Guide to OKRs", "instructor": "Felix Cao", "rating": 4.7, "students": "38K", "duration": "6 hours", "level": "Beginner", "url": "https://www.udemy.com/course/okrs-course/", "skills": ["OKRs", "Goal Setting", "Strategy"]},
                {"title": "Productivity and Time Management for the Overwhelmed", "instructor": "Josh Caban", "rating": 4.5, "students": "95K", "duration": "5 hours", "level": "Beginner", "url": "https://www.udemy.com/course/productivity-and-time-management-for-the-overwhelmed/", "skills": ["Productivity", "Time Management", "Focus"]},
                {"title": "Communication Skills Masterclass", "instructor": "TJ Walker", "rating": 4.4, "students": "62K", "duration": "9 hours", "level": "Beginner", "url": "https://www.udemy.com/course/communication-skills-masterclass/", "skills": ["Communication", "Presentations", "Storytelling"]},
            ],
        },
        "design": {
            "coursera": [
                {"title": "Google UX Design Professional Certificate", "instructor": "Google", "rating": 4.8, "students": "1.5M", "duration": "6 months", "level": "Beginner", "url": "https://www.coursera.org/professional-certificates/google-ux-design", "skills": ["UX Design", "Figma", "Prototyping"]},
                {"title": "UI / UX Design Specialization", "instructor": "CalArts", "rating": 4.6, "students": "320K", "duration": "5 months", "level": "Intermediate", "url": "https://www.coursera.org/specializations/ui-ux-design", "skills": ["UI Design", "UX Research", "Wireframing"]},
            ],
            "udemy": [
                {"title": "User Experience Design Essentials - Adobe XD UI UX Design", "instructor": "Daniel Walter Scott", "rating": 4.6, "students": "220K", "duration": "19 hours", "level": "Beginner", "url": "https://www.udemy.com/course/ui-ux-web-design-using-adobe-xd/", "skills": ["Adobe XD", "UI Design", "UX"]},
                {"title": "Figma UI UX Design Essentials", "instructor": "Daniel Walter Scott", "rating": 4.7, "students": "185K", "duration": "16 hours", "level": "Beginner", "url": "https://www.udemy.com/course/figma-ux-ui-design-user-experience-tutorial-course/", "skills": ["Figma", "Prototyping", "Design Systems"]},
            ],
        },
        "data_science": {
            "coursera": [
                {"title": "IBM Data Science Professional Certificate", "instructor": "IBM", "rating": 4.6, "students": "740K", "duration": "11 months", "level": "Beginner", "url": "https://www.coursera.org/professional-certificates/ibm-data-science", "skills": ["Python", "SQL", "Machine Learning", "Data Visualization"]},
                {"title": "Applied Data Science with Python Specialization", "instructor": "University of Michigan", "rating": 4.5, "students": "480K", "duration": "5 months", "level": "Intermediate", "url": "https://www.coursera.org/specializations/data-science-python", "skills": ["Python", "Pandas", "Matplotlib", "NLP"]},
            ],
            "udemy": [
                {"title": "The Data Science Course: Complete Data Science Bootcamp", "instructor": "365 Careers", "rating": 4.5, "students": "440K", "duration": "29 hours", "level": "Beginner", "url": "https://www.udemy.com/course/the-data-science-course-complete-data-science-bootcamp/", "skills": ["Statistics", "Python", "SQL", "ML"]},
                {"title": "SQL - MySQL for Data Analytics and Business Intelligence", "instructor": "365 Careers", "rating": 4.7, "students": "340K", "duration": "10 hours", "level": "Beginner", "url": "https://www.udemy.com/course/sql-mysql-for-data-analytics-and-business-intelligence/", "skills": ["SQL", "MySQL", "Data Analysis"]},
            ],
        },
        "product": {
            "coursera": [
                {"title": "Digital Product Management", "instructor": "University of Virginia", "rating": 4.7, "students": "290K", "duration": "5 months", "level": "Intermediate", "url": "https://www.coursera.org/specializations/uva-darden-digital-product-management", "skills": ["Product Strategy", "Roadmapping", "User Research"]},
                {"title": "Become a Product Manager", "instructor": "Cole Mercer / Evan Kimbrell", "rating": 4.5, "students": "120K", "duration": "8 months", "level": "Beginner", "url": "https://www.coursera.org/learn/product-management", "skills": ["Product Management", "Agile", "Market Research"]},
            ],
            "udemy": [
                {"title": "Become a Product Manager | Learn the Skills & Get the Job", "instructor": "Cole Mercer", "rating": 4.4, "students": "115K", "duration": "19 hours", "level": "Beginner", "url": "https://www.udemy.com/course/become-a-product-manager-learn-the-skills-get-a-job/", "skills": ["Product Management", "UX", "Analytics"]},
                {"title": "AI Product Management Masterclass", "instructor": "Jared Walker", "rating": 4.6, "students": "28K", "duration": "7 hours", "level": "Intermediate", "url": "https://www.udemy.com/course/ai-product-management/", "skills": ["AI/ML Products", "LLMs", "Strategy"]},
            ],
        },
        "general": {
            "coursera": [
                {"title": "Learning How to Learn", "instructor": "Deep Teaching Solutions", "rating": 4.8, "students": "3.8M", "duration": "4 weeks", "level": "Beginner", "url": "https://www.coursera.org/learn/learning-how-to-learn", "skills": ["Learning Techniques", "Memory", "Focus"]},
                {"title": "The Science of Well-Being", "instructor": "Yale University", "rating": 4.9, "students": "4.1M", "duration": "10 weeks", "level": "Beginner", "url": "https://www.coursera.org/learn/the-science-of-well-being", "skills": ["Wellbeing", "Happiness", "Habit Formation"]},
            ],
            "udemy": [
                {"title": "Master Your Mind: Develop a Growth Mindset", "instructor": "SuperHuman Academy", "rating": 4.6, "students": "65K", "duration": "5 hours", "level": "Beginner", "url": "https://www.udemy.com/course/master-your-mind-develop-a-growth-mindset/", "skills": ["Mindset", "Resilience", "Focus"]},
                {"title": "Mindfulness-Based Stress Reduction", "instructor": "UC San Diego", "rating": 4.7, "students": "42K", "duration": "8 hours", "level": "Beginner", "url": "https://www.udemy.com/course/mindfulness-based-stress-reduction-mbsr/", "skills": ["Mindfulness", "Stress Reduction", "Wellbeing"]},
            ],
        },
    }
    role_lower = (user_role + " " + job_title + " " + department).lower()

    if any(k in role_lower for k in ["engineer", "developer", "software", "tech", "coding", "programmer", "devops", "sre", "backend", "frontend"]):
        primary = "engineering"
        secondary = "management" if any(k in role_lower for k in ["manager", "lead", "head", "director", "vp"]) else "data_science"
    elif any(k in role_lower for k in ["manager", "lead", "head", "director", "vp", "executive", "cto", "ceo", "president"]):
        primary = "management"
        secondary = "product"
    elif any(k in role_lower for k in ["design", "ux", "ui", "creative", "art"]):
        primary = "design"
        secondary = "product"
    elif any(k in role_lower for k in ["data", "analyst", "science", "research", "ml", "ai", "machine learning"]):
        primary = "data_science"
        secondary = "engineering"
    elif any(k in role_lower for k in ["product", "pm", "growth", "strategy"]):
        primary = "product"
        secondary = "management"
    else:
        primary = "general"
        secondary = "management"

    system = (
        "You are a professional L&D (Learning & Development) advisor for enterprise teams. "
        "Your job is to provide highly personalized, role-specific course recommendations. "
        "Return valid JSON only — no markdown, no explanation outside JSON."
    )

    user_msg = (
        f"Professional profile:\n"
        f"- Name: {user_name}\n"
        f"- Role: {user_role}\n"
        f"- Job Title: {job_title}\n"
        f"- Department: {department}\n"
        f"- Recent topics of interest: {', '.join(recent_topics) if recent_topics else 'None specified'}\n\n"
        f"Based on this profile, return a JSON object with:\n"
        f"1. 'ai_insight' (string, 2-3 sentences explaining why these courses are perfect for this person's career growth right now)\n"
        f"2. 'top_skills' (array of 4-5 skill strings this person should focus on)\n"
        f"3. 'learning_path' (string, one sentence describing their ideal 3-6 month learning journey)\n"
        f"4. 'primary_category' (string: one of engineering/management/design/data_science/product/general)\n"
        f"5. 'secondary_category' (string: same options, different from primary)\n"
        f"The returned JSON must be valid and contain exactly these 5 keys."
    )

    ai_data = {}
    try:
        from app.services.sarvam import analyze_json as ai_analyze_json
        ai_data = await ai_analyze_json(system, user_msg)
    except Exception as e:
        logger.error(f"Course recommendation AI error: {e}")

    primary   = ai_data.get("primary_category", primary)
    secondary = ai_data.get("secondary_category", secondary)

    if primary not in COURSE_DB:
        primary = "engineering"
    if secondary not in COURSE_DB:
        secondary = "management"

    coursera_courses = (COURSE_DB[primary]["coursera"][:3] + COURSE_DB[secondary]["coursera"][:1] + COURSE_DB["general"]["coursera"][:1])
    udemy_courses    = (COURSE_DB[primary]["udemy"][:3]    + COURSE_DB[secondary]["udemy"][:1]    + COURSE_DB["general"]["udemy"][:1])

    seen_c, seen_u = set(), set()
    coursera_final, udemy_final = [], []
    for c in coursera_courses:
        if c["title"] not in seen_c:
            seen_c.add(c["title"])
            coursera_final.append(c)
    for u in udemy_courses:
        if u["title"] not in seen_u:
            seen_u.add(u["title"])
            udemy_final.append(u)

    return {
        "coursera": coursera_final[:5],
        "udemy":    udemy_final[:5],
        "ai_insight": ai_data.get("ai_insight", f"Based on your role as {job_title}, these courses are tailored to accelerate your growth in the areas most impactful for your career."),
        "top_skills": ai_data.get("top_skills", ["Leadership", "Technical Excellence", "Strategic Thinking"]),
        "learning_path": ai_data.get("learning_path", f"Focus on deepening expertise in your core domain while developing complementary skills that multiply your impact."),
        "primary_category": primary,
    }
