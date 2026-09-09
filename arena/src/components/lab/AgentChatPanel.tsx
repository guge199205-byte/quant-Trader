/** 右栏「对话」：围绕当前策略问答。P2 只出文本；「让 agent 改策略」是 P3。 */
import { useEffect, useRef, useState } from 'react';
import { STAGE_LABEL } from './format';
import type { Workbench } from './useWorkbench';

/** 空白对话时的快捷提问——把「不知道怎么问」的空白填掉 */
const QUICK = [
  '这个策略的核心逻辑是什么？',
  '适合什么行情？最大的风险在哪？',
  '哪个参数最敏感？过拟合风险如何？',
];

export default function AgentChatPanel({ wb }: { wb: Workbench }) {
  const [text, setText] = useState('');
  const logRef = useRef<HTMLDivElement>(null);
  const msgs = wb.chatPending ? [...wb.chat, wb.chatPending] : wb.chat;

  // 新消息/阶段变化时滚到底部（答案比较长，不滚就看不见）
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs.length, wb.chatStage]);

  const send = (value: string) => {
    const t = value.trim();
    if (!t || wb.chatBusy) return;
    wb.sendChat(t);
    setText('');
  };

  return (
    <div className="lab-chat">
      <div className="lab-chat-log" ref={logRef}>
        {!msgs.length && (
          <div className="lab-empty">
            问策略逻辑、适用行情、参数敏感度——agent 会读到当前 Pine 源码、你的备注与最近一次回测。
          </div>
        )}
        {msgs.map((m, i) => (
          <div
            key={`${m.job_id ?? 'local'}-${m.role}-${i}`}
            className={`lab-msg ${m.role}${m.pending ? ' pending' : ''}`}
          >
            <div className="lab-msg-role">{m.role === 'user' ? '我' : 'Agent'}</div>
            <div className="lab-msg-body">{m.content}</div>
          </div>
        ))}
        {wb.chatStage && (
          <div className="lab-msg assistant">
            <div className="lab-msg-role">Agent</div>
            <div className="lab-msg-body lab-typing">
              {STAGE_LABEL[wb.chatStage] ?? wb.chatStage}…
            </div>
          </div>
        )}
      </div>

      {wb.chatErr && <div className="lab-err">{wb.chatErr}</div>}

      {!msgs.length && (
        <div className="lab-chat-quick">
          {QUICK.map((q) => (
            <button key={q} className="lab-more" disabled={wb.chatBusy} onClick={() => send(q)}>
              {q}
            </button>
          ))}
        </div>
      )}

      <div className="lab-chat-form">
        <textarea
          className="lab-chat-input"
          rows={2}
          value={text}
          disabled={wb.chatBusy}
          placeholder={wb.chatBusy ? '等 agent 答完再发下一条…' : '问点什么（Enter 发送，Shift+Enter 换行）'}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send(text);
            }
          }}
        />
        <button
          className="lab-run lab-chat-send"
          disabled={wb.chatBusy || !text.trim()}
          onClick={() => send(text)}
        >
          {wb.chatBusy ? '思考中…' : '发送'}
        </button>
      </div>

      <p className="lab-note">
        回答由宿主 worker 调模型产出（容器不执行模型代码，也没有 bwrap）；
        「让 agent 改策略」是 P3：产出候选 → 沙箱回测 → 你点「采用」才写回源码。
      </p>
    </div>
  );
}
