import { MarketId, marketMeta } from '../api/client';
import { fmtMoney, fmtPct, pnlClass } from '../utils/format';

/** 模型 emoji 标识（终端风色块映射） */
export const MODEL_LOGOS: Record<string, string> = {
  'deepseek-v4-flash': '🟠',
  'deepseek-v4-pro': '🔵',
};

export const logoOf = (name: string): string => MODEL_LOGOS[name] ?? '⚪';

/** 模型名 → 短标签（终端显示） */
export const shortName = (name: string): string =>
  name
    .replace('deepseek-v4', 'DS V4')
    .replace('deepseek', 'DS')
    .toUpperCase();

/** mini 模型卡：logo + 名称 + 余额 + 收益% —— 终端风 model-card-mini。
 *  点击触发选中（右栏 filter 联动）。 */
export default function ModelCard({
  market,
  agent,
  balance,
  ret,
  selected,
  onClick,
}: {
  market: MarketId;
  agent: string;
  balance: number | null;
  ret: number | null;
  selected: boolean;
  onClick: () => void;
}) {
  const meta = marketMeta(market);
  return (
    <div className={`model-card-mini ${selected ? 'selected' : ''}`} onClick={onClick}>
      <div className="model-logo">{logoOf(agent)}</div>
      <div className="model-info">
        <div className="model-name">{shortName(agent)}</div>
        <div className="model-balance">{fmtMoney(balance, meta.currency)}</div>
        <div className={`model-pnl ${pnlClass(ret)}`}>{fmtPct(ret)}</div>
      </div>
    </div>
  );
}
