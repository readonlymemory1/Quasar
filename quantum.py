
import time
import pandas as pd
import matplotlib.pyplot as plt

from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

# =====================================
# 1. 양자 회로 구성
# =====================================

qc = QuantumCircuit(1, 1)
qc.h(0)
qc.measure(0, 0)

simulator = AerSimulator()

# =====================================
# 2. 실험 조건
# =====================================

shots_list = [
    100, 500, 1000, 2000, 5000,
    10000, 20000, 30000, 40000, 50000
]

repeats = 30
target_error = 1.0
target_success_rate = 95.0

results = []

# =====================================
# 3. 반복 실험
# =====================================

for shots in shots_list:

    print(f"{shots:,} shots 실험 중...")

    for trial in range(repeats):

        start = time.perf_counter()

        job = simulator.run(qc, shots=shots)
        counts = job.result().get_counts()

        elapsed = time.perf_counter() - start

        zero_count = counts.get('0', 0)
        one_count = counts.get('1', 0)

        zero_percent = zero_count / shots * 100
        error = abs(zero_percent - 50)

        results.append({
            "측정횟수": shots,
            "반복번호": trial + 1,
            "0횟수": zero_count,
            "1횟수": one_count,
            "0비율(%)": zero_percent,
            "오차(%p)": error,
            "실행시간(초)": elapsed
        })

# =====================================
# 4. 측정횟수별 평균 분석
# =====================================

df = pd.DataFrame(results)

summary = df.groupby("측정횟수").agg(
    평균오차=("오차(%p)", "mean"),
    평균실행시간=("실행시간(초)", "mean"),
    목표달성률=(
        "오차(%p)",
        lambda x: (x <= target_error).mean() * 100
    )
).reset_index()

print("\n========== 실험 결과 ==========")
print(summary.round(4).to_string(index=False))

# =====================================
# 5. 최적 측정 횟수 계산
# =====================================

valid = summary[
    (summary["평균오차"] <= target_error) &
    (summary["목표달성률"] >= target_success_rate)
]

print("\n========== 최적값 분석 ==========")

if not valid.empty:

    # 조건을 만족하는 가장 작은 측정 횟수
    best = valid.sort_values("측정횟수").iloc[0]

    optimal_shots = int(best["측정횟수"])

    print(f"최적 측정 횟수: {optimal_shots:,} shots")
    print(f"평균 오차: {best['평균오차']:.4f}%p")
    print(f"목표 달성률: {best['목표달성률']:.2f}%")
    print(f"평균 실행 시간: {best['평균실행시간']:.6f}초")

else:

    print("현재 실험 범위에서 조건을 만족하는 측정 횟수가 없습니다.")

    # 조건을 만족하지 못한 경우 평균 오차가 가장 작은 값 표시
    best = summary.sort_values("평균오차").iloc[0]

    print(f"평균 오차가 가장 작은 샷 수: {int(best['측정횟수']):,}")
    print(f"평균 오차: {best['평균오차']:.4f}%p")

# =====================================
# 6. 평균 오차 그래프
# =====================================

plt.figure(figsize=(9, 5))

plt.plot(
    summary["측정횟수"],
    summary["평균오차"],
    marker="o"
)

plt.axhline(
    y=target_error,
    linestyle="--",
    label="Target error: 1%p"
)

plt.xscale("log")
plt.xlabel("Number of shots")
plt.ylabel("Mean absolute error (%p)")
plt.title("Shots vs Mean Error")
plt.legend()
plt.grid(True)
plt.show()

# =====================================
# 7. 목표 달성률 그래프
# =====================================

plt.figure(figsize=(9, 5))

plt.plot(
    summary["측정횟수"],
    summary["목표달성률"],
    marker="o"
)

plt.axhline(
    y=target_success_rate,
    linestyle="--",
    label="Target success rate: 95%"
)

plt.xscale("log")
plt.xlabel("Number of shots")
plt.ylabel("Success rate (%)")
plt.title("Shots vs Success Rate")
plt.legend()
plt.grid(True)
plt.show()

# =====================================
# 8. 실행 시간 그래프
# =====================================

plt.figure(figsize=(9, 5))

plt.plot(
    summary["측정횟수"],
    summary["평균실행시간"],
    marker="o"
)

plt.xscale("log")
plt.xlabel("Number of shots")
plt.ylabel("Average execution time (seconds)")
plt.title("Shots vs Execution Time")
plt.grid(True)
plt.show()

# =====================================
# 9. 결과 저장
# =====================================

df.to_csv("quantum_experiment_detail.csv", index=False)
summary.to_csv("quantum_experiment_summary.csv", index=False)

print("\n실험 데이터 저장 완료!")
