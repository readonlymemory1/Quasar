
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator
import matplotlib.pyplot as plt
import pandas as pd

# 1. 양자 회로 만들기
qc = QuantumCircuit(1, 1)
qc.h(0)          # 하다마드 게이트
qc.measure(0, 0) # 큐비트 측정

# 2. 시뮬레이터 준비
simulator = AerSimulator()

# 3. 측정 횟수별 실험
shots_list = [10, 100, 1000, 5000, 10000]
results = []

for shots in shots_list:
    job = simulator.run(qc, shots=shots)
    counts = job.result().get_counts()

    zero_count = counts.get('0', 0)
    one_count = counts.get('1', 0)

    zero_percent = zero_count / shots * 100
    error = abs(zero_percent - 50)

    results.append({
        "측정 횟수": shots,
        "0 횟수": zero_count,
        "1 횟수": one_count,
        "0 비율(%)": round(zero_percent, 2),
        "50%와의 오차(%p)": round(error, 2)
    })

# 4. 결과 표 출력
df = pd.DataFrame(results)
print(df.to_string(index=False))

# 5. 측정 횟수와 오차 그래프
plt.plot(df["측정 횟수"], df["50%와의 오차(%p)"], marker="o")
plt.xscale("log")
plt.xlabel("Number of shots")
plt.ylabel("Absolute error (%p)")
plt.title("Measurement shots vs. error")
plt.grid(True)
plt.show()
