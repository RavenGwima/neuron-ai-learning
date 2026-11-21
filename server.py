import json
import logging
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent
logging.basicConfig(
    filename=str(BASE_DIR / "neuron-server.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODELS = {
    "OPENROUTER_CHAT_MODEL": "openai/gpt-6-luna",
    "OPENROUTER_QUESTION_MODEL": "deepseek/deepseek-v4.1-flash-1017",
}
MAX_BODY_SIZE = 16_384
RANKS = ("SD", "SMP", "SMA", "Mahasiswa", "Pakar", "Ilmuwan")
RANK_DIFFICULTY = {
    "SD": "Konsep dasar dengan contoh konkret dan paling banyak satu langkah penalaran.",
    "SMP": "Penerapan konsep dengan satu atau dua langkah dan hubungan sebab-akibat sederhana.",
    "SMA": "Penerapan beberapa konsep atau rumus dengan penalaran bertahap.",
    "Mahasiswa": "Analisis abstrak atau penerapan beberapa konsep yang saling terkait.",
    "Pakar": "Kasus tingkat lanjut yang memerlukan analisis mendalam, strategi, atau evaluasi bukti; bukan soal definisi dasar.",
    "Ilmuwan": "Sintesis tingkat sangat lanjut, penilaian asumsi atau data, dan penalaran kompleks; tetap dapat dijawab dari informasi soal.",
}
QUESTION_ATTEMPTS = 2
GRADES = ("SD", "SMP", "SMA/SMK", "Kuliah")
SUBJECTS = {
    "math": "Matematika",
    "indonesian": "Bahasa Indonesia",
    "english": "Bahasa Inggris",
    "ipa": "IPA Terpadu",
    "physics": "Fisika",
    "chemistry": "Kimia",
    "biology": "Biologi",
    "history": "Sejarah",
    "geography": "Geografi",
    "economics": "Ekonomi",
    "sociology": "Sosiologi",
    "civics": "PPKn",
    "informatics": "Informatika",
    "religion": "Pendidikan Agama",
    "arabic": "Bahasa Arab",
    "arts": "Seni Budaya",
    "pe": "PJOK",
    "craft": "Prakarya",
    "regional": "Bahasa Daerah",
    "ips": "IPS Terpadu",
    "entrepreneurship": "Kewirausahaan",
}


def load_dotenv():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        name, value = entry.split("=", 1)
        name = name.strip()
        value = value.strip().strip("\"'")
        if name and name not in os.environ:
            os.environ[name] = value


def openrouter_messages(messages, model_setting, json_mode=False):
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    model = os.environ.get(model_setting, "").strip() or DEFAULT_MODELS.get(model_setting, "")
    if not api_key or not model:
        raise RuntimeError(
            "Server belum dikonfigurasi. Isi OPENROUTER_API_KEY dan {} di file .env.".format(model_setting)
        )
    if model.endswith(":batch"):
        raise RuntimeError(
            "{} memakai varian :batch yang berjalan asinkron. "
            "Gunakan ID model tanpa :batch untuk chat dan soal langsung.".format(model_setting)
        )

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.25 if json_mode else 0.7,
        "max_tokens": 900,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    request = Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "HTTP-Referer": (
                os.environ.get("PUBLIC_APP_URL")
                or os.environ.get("RENDER_EXTERNAL_URL")
                or "http://localhost:8000"
            ),
            "X-Title": "Neuron Belajar",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RuntimeError("OpenRouter menolak permintaan (HTTP {}).".format(error.code))
    except URLError:
        raise RuntimeError("Tidak dapat terhubung ke OpenRouter. Periksa jaringan server.")
    except TimeoutError:
        raise RuntimeError("OpenRouter terlalu lama merespons. Coba lagi.")

    try:
        return result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Respons OpenRouter tidak memiliki jawaban yang dapat dibaca.")


def require_string(data, name, maximum):
    value = data.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("{} harus berupa teks 1–{} karakter.".format(name, maximum))
    return value.strip()


def parse_question_response(content):
    if not isinstance(content, str) or not content.strip():
        raise ValueError("AI mengirim jawaban kosong.")
    candidate = content.strip()
    try:
        generated = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Jawaban AI tidak berisi objek JSON.")
        try:
            generated = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as error:
            raise ValueError("JSON dari AI tidak dapat dibaca.") from error
    if not isinstance(generated, dict):
        raise ValueError("JSON soal dari AI harus berupa objek.")
    return generated


def normalize_question(generated, subject_id):
    generated_subject = generated.get("subjectId", generated.get("subject_id", subject_id))
    if generated_subject != subject_id:
        raise ValueError("AI mengirim soal yang tidak sesuai dengan mapel pilihan.")

    question = generated.get("question", generated.get("q"))
    choices = generated.get("choices", generated.get("options"))
    correct_index = generated.get("correctIndex", generated.get("correct_index"))
    if isinstance(correct_index, str) and correct_index.isdigit():
        correct_index = int(correct_index)
    if (
        not isinstance(question, str)
        or not question.strip()
        or len(question) > 1200
        or not isinstance(choices, list)
        or len(choices) != 4
        or any(not isinstance(choice, str) or not choice.strip() or len(choice) > 300 for choice in choices)
        or len({choice.strip().casefold() for choice in choices}) != 4
        or not isinstance(correct_index, int)
        or isinstance(correct_index, bool)
        or correct_index < 0
        or correct_index >= len(choices)
    ):
        raise ValueError("Struktur soal AI tidak sesuai format.")
    return {
        "subjectId": subject_id,
        "q": question.strip(),
        "a": [choice.strip() for choice in choices],
        "c": correct_index,
    }


def make_question(data):
    subject_id = require_string(data, "subject_id", 40)
    if subject_id not in SUBJECTS:
        raise ValueError("Mata pelajaran tidak dikenal.")
    rank = require_string(data, "rank", 20)
    if rank not in RANKS:
        raise ValueError("Rank tidak dikenal.")
    recent = data.get("recent_questions", [])
    if not isinstance(recent, list) or any(not isinstance(item, str) for item in recent):
        raise ValueError("recent_questions harus berupa daftar teks.")
    recent = [item[:400] for item in recent[-20:]]
    subject = SUBJECTS[subject_id]

    prompt = (
        "Buat satu soal pilihan ganda baru yang hanya menguji mata pelajaran "
        + subject
        + ". Rank target: "
        + rank
        + ". Standar kesulitan wajib: "
        + RANK_DIFFICULTY[rank]
        + " Semakin tinggi rank, semakin sulit penalarannya; jangan menurunkan soal "
        "Pakar atau Ilmuwan menjadi pertanyaan hafalan tingkat SD. Gunakan bahasa Indonesia "
        "yang jelas dan jangan mencampurkan mata pelajaran lain. Hindari mengulang soal "
        "berikut: "
        + json.dumps(recent, ensure_ascii=False)
        + '. Kembalikan hanya satu objek JSON tanpa markdown, tepat dengan bentuk {"subjectId":"'
        + subject_id
        + '","question":"teks soal","choices":["opsi 1","opsi 2","opsi 3","opsi 4"],'
        '"correctIndex":0}. Harus tepat empat opsi berbeda dan hanya satu jawaban benar. '
        "correctIndex adalah indeks berbasis nol: 0 berarti opsi pertama, 3 berarti opsi keempat. "
        "Pastikan soal sesuai mapel dan rank, kunci jawaban benar secara faktual, serta pilihan salah masuk akal."
    )
    messages = [
        {
            "role": "system",
            "content": "Anda membuat soal pendidikan yang akurat. Ikuti skema JSON, tingkat kesulitan, dan cakupan mapel secara ketat.",
        },
        {"role": "user", "content": prompt},
    ]
    validation_error = ""
    for attempt in range(QUESTION_ATTEMPTS):
        attempt_messages = list(messages)
        if attempt:
            attempt_messages.append(
                {
                    "role": "user",
                    "content": (
                        "Buat ulang soalnya. Respons sebelumnya gagal validasi: "
                        + validation_error
                        + ". Keluarkan hanya JSON valid dengan empat opsi dan correctIndex berbasis nol."
                    ),
                }
            )
        try:
            content = openrouter_messages(
                attempt_messages,
                "OPENROUTER_QUESTION_MODEL",
                json_mode=True,
            )
            return normalize_question(parse_question_response(content), subject_id)
        except ValueError as error:
            validation_error = str(error)
            logging.warning(
                "Invalid generated question for %s/%s on attempt %s/%s: %s",
                subject_id,
                rank,
                attempt + 1,
                QUESTION_ATTEMPTS,
                validation_error,
            )
    raise RuntimeError("AI gagal membuat soal dengan format yang benar setelah dicoba ulang. Coba buat soal lagi.")


def tutor_reply(data):
    subject_id = require_string(data, "subject_id", 40)
    if subject_id not in SUBJECTS:
        raise ValueError("Mata pelajaran tidak dikenal.")
    grade = require_string(data, "grade", 20)
    if grade not in GRADES:
        raise ValueError("Tingkat belajar tidak dikenal.")
    conversation = data.get("conversation", [])
    if not isinstance(conversation, list) or len(conversation) > 12:
        raise ValueError("Riwayat percakapan tidak valid.")

    messages = [
        {
            "role": "system",
            "content": (
                "Anda tutor belajar yang ramah dan teliti. Bantu murid tingkat "
                + grade
                + " memahami "
                + SUBJECTS[subject_id]
                + " saja. Jangan mengganti atau mencampur mata pelajaran. Jelaskan "
                "langkah demi langkah, akui jika tidak yakin, dan jangan mengarang fakta."
            ),
        }
    ]
    for item in conversation:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            messages.append({"role": item["role"], "content": content[:1500]})
    if not any(message["role"] == "user" for message in messages[1:]):
        raise ValueError("Kirim pertanyaan terlebih dahulu.")
    return {"reply": openrouter_messages(messages, "OPENROUTER_CHAT_MODEL")}


class NeuronHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self):
        if urlsplit(self.path).path == "/api/health":
            api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
            chat_model = os.environ.get("OPENROUTER_CHAT_MODEL", "").strip() or DEFAULT_MODELS["OPENROUTER_CHAT_MODEL"]
            question_model = os.environ.get("OPENROUTER_QUESTION_MODEL", "").strip() or DEFAULT_MODELS["OPENROUTER_QUESTION_MODEL"]
            configured = bool(
                api_key
                and not api_key.startswith("isi_dengan_")
                and not chat_model.endswith(":batch")
                and not question_model.endswith(":batch")
            )
            self.send_json(200, {"ok": True, "configured": configured})
            return
        super().do_GET()

    def do_POST(self):
        route = urlsplit(self.path).path
        if route not in ("/api/questions", "/api/chat"):
            self.send_json(404, {"error": "Rute API tidak ditemukan."})
            return
        origin = self.headers.get("Origin")
        if origin:
            origin_url = urlsplit(origin)
            request_host = self.headers.get("Host", "").strip()
            local_origins = ("http://127.0.0.1:8000", "http://localhost:8000")
            same_origin = origin_url.scheme in ("http", "https") and origin_url.netloc == request_host
            if origin not in local_origins and not same_origin:
                self.send_json(403, {"error": "Permintaan lintas situs tidak diizinkan."})
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_SIZE:
                raise ValueError("Ukuran permintaan harus di bawah 16 KB.")
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Format permintaan tidak valid.")
            result = make_question(data) if route == "/api/questions" else tutor_reply(data)
            self.send_json(200, result)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "Permintaan harus berupa JSON yang valid."})
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except RuntimeError as error:
            logging.error("%s: %s", route, error)
            self.send_json(502, {"error": str(error)})

    def log_message(self, format, *args):
        logging.info("%s - %s", self.address_string(), format % args)

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    load_dotenv()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), NeuronHandler)
    logging.info("Neuron berjalan di %s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Server dihentikan.")
    finally:
        server.server_close()
