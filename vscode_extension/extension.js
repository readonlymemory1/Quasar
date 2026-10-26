const http = require("http");
const path = require("path");
const vscode = require("vscode");

const endpointKey = "quasar.endpoint";
const tokenKey = "quasar.token";

function activate(context) {
    const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    status.command = "quasar.connectLiveAnalysis";
    status.text = "$(broadcast) Quasar: 연결 안 됨";
    status.tooltip = "Quasar 실시간 분석 연결 설정";
    status.show();
    context.subscriptions.push(status);

    let connected = false;
    let pollingEditorUpdate = false;
    const pending = new Map();

    async function postEditorUpdateAcknowledgment(endpoint, token, updateId, result) {
        const acknowledgmentUrl = new URL(endpoint);
        acknowledgmentUrl.pathname = "/editor-update/ack";
        acknowledgmentUrl.search = "";
        acknowledgmentUrl.hash = "";
        await new Promise((resolve, reject) => {
            const body = Buffer.from(JSON.stringify({ id: updateId, status: result }));
            const request = http.request(acknowledgmentUrl, {
                method: "POST",
                headers: {
                    "Authorization": `Bearer ${token}`,
                    "Content-Type": "application/json",
                    "Content-Length": body.length,
                },
                timeout: 3000,
            }, (response) => {
                response.resume();
                response.on("end", () => {
                    if (response.statusCode === 204) {
                        resolve();
                    } else {
                        reject(new Error(`Update acknowledgment failed: HTTP ${response.statusCode}`));
                    }
                });
            });
            request.on("timeout", () => request.destroy(new Error("Request timed out")));
            request.on("error", reject);
            request.end(body);
        });
    }

    async function pollEditorUpdate() {
        if (!connected || pollingEditorUpdate) {
            return;
        }
        pollingEditorUpdate = true;
        try {
            const endpoint = context.globalState.get(endpointKey);
            const token = await context.secrets.get(tokenKey);
            if (!endpoint || !token) {
                return;
            }
            const updateUrl = new URL(endpoint);
            updateUrl.pathname = "/editor-update";
            updateUrl.search = "";
            updateUrl.hash = "";
            const update = await new Promise((resolve, reject) => {
                const request = http.request(updateUrl, {
                    method: "GET",
                    headers: { "Authorization": `Bearer ${token}` },
                    timeout: 3000,
                }, (response) => {
                    let body = "";
                    response.setEncoding("utf8");
                    response.on("data", (chunk) => { body += chunk; });
                    response.on("end", () => {
                        if (response.statusCode === 204) {
                            resolve(null);
                        } else if (response.statusCode === 200) {
                            try {
                                resolve(JSON.parse(body));
                            } catch (error) {
                                reject(error);
                            }
                        } else {
                            reject(new Error(`Editor update request failed: HTTP ${response.statusCode}`));
                        }
                    });
                });
                request.on("timeout", () => request.destroy(new Error("Request timed out")));
                request.on("error", reject);
                request.end();
            });
            if (!update) {
                return;
            }

            const document = vscode.workspace.textDocuments.find(
                (candidate) => path.resolve(candidate.uri.fsPath).toLowerCase() === path.resolve(update.path).toLowerCase(),
            );
            if (!document || document.getText() !== update.base_source) {
                await postEditorUpdateAcknowledgment(endpoint, token, update.id, "conflict");
                status.text = "$(warning) Quasar: 편집 충돌";
                status.tooltip = "수정안 대기 중 파일이 변경되어 적용하지 않았습니다.";
                return;
            }

            const edit = new vscode.WorkspaceEdit();
            edit.replace(
                document.uri,
                new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)),
                update.source,
            );
            const applied = await vscode.workspace.applyEdit(edit);
            if (!applied) {
                await postEditorUpdateAcknowledgment(endpoint, token, update.id, "conflict");
                status.text = "$(warning) Quasar: 편집 실패";
                status.tooltip = "VS Code에서 수정안을 적용하지 못했습니다.";
                return;
            }

            if (update.save) {
                const saved = await document.save();
                await postEditorUpdateAcknowledgment(
                    endpoint,
                    token,
                    update.id,
                    saved ? "saved" : "save_failed",
                );
                status.text = saved
                    ? "$(check) Quasar: 저장 완료"
                    : "$(warning) Quasar: 저장 실패";
                status.tooltip = saved
                    ? "수정안을 적용하고 파일을 저장했습니다."
                    : "수정안은 적용했지만 파일 저장에 실패했습니다.";
            } else {
                await postEditorUpdateAcknowledgment(endpoint, token, update.id, "applied");
            }
        } catch (error) {
            status.text = "$(warning) Quasar: 연결 오류";
            status.tooltip = `공동작업 수정안 수신 실패: ${error.message}`;
        } finally {
            pollingEditorUpdate = false;
        }
    }

    async function sendDocument(document) {
        if (!connected || !document || document.languageId !== "python") {
            return;
        }

        const workspace = vscode.workspace.getWorkspaceFolder(document.uri);
        if (!workspace || !document.uri.fsPath.toLowerCase().endsWith(".py")) {
            return;
        }

        const endpoint = context.globalState.get(endpointKey);
        const token = await context.secrets.get(tokenKey);
        if (!endpoint || !token) {
            return;
        }

        const relativePath = path.relative(workspace.uri.fsPath, document.uri.fsPath);
        if (relativePath === ".." || relativePath.startsWith(`..${path.sep}`)) {
            return;
        }

        const body = Buffer.from(JSON.stringify({
            path: document.uri.fsPath,
            source: document.getText(),
        }));

        await new Promise((resolve) => {
            const request = http.request(endpoint, {
                method: "POST",
                headers: {
                    "Authorization": `Bearer ${token}`,
                    "Content-Type": "application/json",
                    "Content-Length": body.length,
                },
                timeout: 3000,
            }, (response) => {
                response.resume();
                response.on("end", () => {
                    if (response.statusCode === 204) {
                        status.text = "$(broadcast) Quasar: 연결됨";
                        status.tooltip = `실시간 분석: ${path.basename(document.uri.fsPath)}`;
                    } else {
                        status.text = "$(warning) Quasar: 전송 실패";
                        status.tooltip = `Quasar 응답: HTTP ${response.statusCode}`;
                    }
                    resolve();
                });
            });
            request.on("timeout", () => request.destroy(new Error("Request timed out")));
            request.on("error", () => {
                status.text = "$(warning) Quasar: 연결 오류";
                status.tooltip = "Quasar 앱이 실행 중인지 확인하세요.";
                resolve();
            });
            request.end(body);
        });
    }

    function scheduleDocument(document, delay = 250) {
        if (!document || document.languageId !== "python") {
            return;
        }
        const key = document.uri.toString();
        clearTimeout(pending.get(key));
        pending.set(key, setTimeout(() => {
            pending.delete(key);
            void sendDocument(document);
        }, delay));
    }

    const syncTimer = setInterval(() => {
        if (connected && vscode.window.activeTextEditor) {
            scheduleDocument(vscode.window.activeTextEditor.document, 0);
        }
    }, 10000);
    const editorUpdateTimer = setInterval(() => {
        void pollEditorUpdate();
    }, 1000);
    context.subscriptions.push({
        dispose() {
            clearInterval(syncTimer);
            clearInterval(editorUpdateTimer);
        },
    });

    async function connect() {
        const endpoint = await vscode.window.showInputBox({
            title: "Quasar 로컬 연결 주소",
            prompt: "Quasar 화면에 표시된 Endpoint를 입력하세요.",
            value: context.globalState.get(endpointKey, ""),
            ignoreFocusOut: true,
            validateInput(value) {
                try {
                    const url = new URL(value);
                    if (url.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(url.hostname)) {
                        return "127.0.0.1 또는 localhost의 HTTP 주소만 허용됩니다.";
                    }
                    return null;
                } catch {
                    return "올바른 URL을 입력하세요.";
                }
            },
        });
        if (!endpoint) {
            return;
        }

        const token = await vscode.window.showInputBox({
            title: "Quasar 세션 인증 토큰",
            prompt: "Quasar 화면에 표시된 세션 토큰을 입력하세요.",
            password: true,
            ignoreFocusOut: true,
            validateInput(value) {
                return value.trim() ? null : "토큰을 입력해야 합니다.";
            },
        });
        if (!token) {
            return;
        }

        await context.globalState.update(endpointKey, endpoint);
        await context.secrets.store(tokenKey, token.trim());
        connected = true;
        status.text = "$(sync) Quasar: 연결 중";
        scheduleDocument(vscode.window.activeTextEditor && vscode.window.activeTextEditor.document, 0);
        vscode.window.showInformationMessage("Quasar 실시간 분석 연결을 시작했습니다.");
    }

    async function disconnect() {
        connected = false;
        await context.secrets.delete(tokenKey);
        await context.globalState.update(endpointKey, undefined);
        for (const timer of pending.values()) {
            clearTimeout(timer);
        }
        pending.clear();
        status.text = "$(broadcast) Quasar: 연결 안 됨";
        status.tooltip = "Quasar 실시간 분석 연결 설정";
        vscode.window.showInformationMessage("Quasar 실시간 분석 연결을 종료했습니다.");
    }

    context.subscriptions.push(
        vscode.commands.registerCommand("quasar.connectLiveAnalysis", connect),
        vscode.commands.registerCommand("quasar.disconnectLiveAnalysis", disconnect),
        vscode.workspace.onDidChangeTextDocument((event) => {
            if (vscode.window.activeTextEditor &&
                event.document.uri.toString() === vscode.window.activeTextEditor.document.uri.toString()) {
                scheduleDocument(event.document);
            }
        }),
        vscode.window.onDidChangeActiveTextEditor((editor) => {
            if (editor) {
                scheduleDocument(editor.document, 0);
            }
        }),
        vscode.workspace.onDidSaveTextDocument((document) => scheduleDocument(document, 0)),
    );

    void context.secrets.get(tokenKey).then((token) => {
        if (token && context.globalState.get(endpointKey)) {
            connected = true;
            status.text = "$(sync) Quasar: 연결 중";
            scheduleDocument(vscode.window.activeTextEditor && vscode.window.activeTextEditor.document, 0);
        }
    });
}

function deactivate() {}

module.exports = { activate, deactivate };
