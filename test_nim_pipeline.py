"""NVIDIA NIM 음성 인식 엔드투엔드 진단 스크립트.

edge-tts로 한국어 문장을 합성한 뒤, Quasar.py의 실제 전처리 코드
(process_voice_input)와 실제 NimSpeechRecognizer.transcribe 코드를
그대로 돌려 결과를 확인한다.
"""
import asyncio
import json
import sys

sys.path.insert(0, ".")
import edge_tts  # noqa: E402

import Quasar  # noqa: E402


async def synth(text: str, path: str) -> None:
    tts = edge_tts.Communicate(text, "ko-KR-HyunsuNeural")
    await tts.save(path)


def mp3_to_samples(path: str, target_rate: int) -> tuple[list[int], int]:
    import av
    import numpy as np

    container = av.open(path)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=target_rate)
    chunks: list[bytes] = []
    rate = target_rate
    for frame in container.decode(audio=0):
        out = resampler.resample(frame)[0]
        chunks.append(bytes(out.planes[0]))
        rate = out.sample_rate
    raw = b"".join(chunks)
    return np.frombuffer(raw, dtype=np.int16), rate


async def main() -> None:
    from PySide6.QtCore import QSettings

    with open("test_kr_expected.txt", "w", encoding="utf-8") as f:
        f.write("크롬 브라우저 켜봐")
    sample_path = "test_kr_sample.mp3"
    print("[1/4] edge-tts 한국어 문장 합성 중:", flush=True)
    await synth("크롬 브라우저 켜봐", sample_path)

    print("[2/4] 16kHz 단일채널 정수 PCM으로 변환 중:", flush=True)
    import numpy as np

    samples_int16, rate = mp3_to_samples(sample_path, Quasar.VOICE_RECOGNITION_SAMPLE_RATE)
    print(f"    samples={len(samples_int16)}, rate={rate}", flush=True)

    print("[3/4] Quasar 전처리(process_voice_input) 복제:", flush=True)
    samples = Quasar.process_voice_input(
        samples_int16.tobytes(),
        rate,
    )

    print("[4/4] 실제 NimSpeechRecognizer 코드로 인식:", flush=True)
    settings = QSettings("Quasar", "Quasar")
    api_key = (settings.value("speech/nim_api_key", "", type=str) or "").strip()
    if not api_key:
        print("API 키 없음: 설정 확인 필요")
        return
    recognizer = Quasar.NimSpeechRecognizer(api_key)
    signals = Quasar.AppSignals()
    received: dict[str, list[str]] = {"ready": [], "conv": [], "fail": [], "status": []}
    signals.voice_text_ready.connect(lambda t: received["ready"].append(t))
    signals.conversation_voice_text_ready.connect(lambda t: received["conv"].append(t))
    signals.voice_failed.connect(lambda e: received["fail"].append(e))
    signals.conversation_voice_failed.connect(lambda e: received["conv"].append("FAIL:" + e))
    signals.voice_status.connect(lambda s: received["status"].append(s))

    recognizer.transcribe(samples, signals)

    print(json.dumps(received, ensure_ascii=False, indent=2))

    for error in received["fail"] + [e for e in received["conv"] if e.startswith("FAIL:")]:
        print("실패:", error)
    if received["ready"] or received["conv"]:
        print("성공:", received["ready"] or received["conv"])


if __name__ == "__main__":
    asyncio.run(main())
