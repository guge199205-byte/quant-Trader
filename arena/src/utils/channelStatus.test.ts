import { describe, expect, it } from 'vitest';
import type { RealAccountSummary } from '../api/client';
import {
  LAG_SEC,
  channelsOfMarket,
  fmtAgeSec,
  freshnessState,
  futuChannel,
  ibkrChannel,
  qmtChannel,
  tdxChannel,
} from './channelStatus';

const summary = (over: Partial<RealAccountSummary> = {}): RealAccountSummary => ({
  account: 'tdx',
  account_id: 'tdx-default-00000001',
  label: '通达信桥',
  channel: '通达信（BayMax 实盘执行通道）',
  ts: '2026-09-10T00:55:23',
  age_sec: 20,
  total_asset: 921361.64,
  cash: 862621.64,
  market_value: 58740,
  source: 'tdx_bridge',
  position_count: 2,
  ...over,
});

const HEALTHY_TDX = {
  enabled: true,
  bridge_url: 'http://192.168.31.13:8550',
  bridge_token_configured: true,
  real_trading_enabled: true,
  health: { status: 'ok', tdx_connected: true },
};

describe('fmtAgeSec', () => {
  it('按量级换单位', () => {
    expect(fmtAgeSec(22)).toBe('22 秒前');
    expect(fmtAgeSec(600)).toBe('10 分钟前');
    expect(fmtAgeSec(5400)).toBe('1.5 小时前');
    expect(fmtAgeSec(172800)).toBe('2.0 天前');
  });

  it('拿不到 → 破折号', () => {
    expect(fmtAgeSec(null)).toBe('—');
    expect(fmtAgeSec(NaN)).toBe('—');
  });
});

describe('freshnessState', () => {
  it('三档：实时 / 滞后 / 停更', () => {
    expect(freshnessState(30)).toBe('ok');
    expect(freshnessState(180)).toBe('ok');
    expect(freshnessState(600)).toBe('warn');
    expect(freshnessState(LAG_SEC)).toBe('warn');
    expect(freshnessState(LAG_SEC + 1)).toBe('off');
  });

  it('无快照 → unknown（不是 off：没数据 ≠ 掉线）', () => {
    expect(freshnessState(null)).toBe('unknown');
  });
});

describe('tdxChannel', () => {
  it('桥在线 + 客户端登录 + 快照新鲜 → 在线', () => {
    const c = tdxChannel(HEALTHY_TDX as never, summary());
    expect(c.state).toBe('ok');
    expect(c.stateText).toBe('在线');
    expect(c.lines.find((l) => l.k === '下单权限')?.v).toContain('允许');
  });

  it('桥在线但账户停更（09-10 真实故障态）→ 不报在线', () => {
    const c = tdxChannel(HEALTHY_TDX as never, summary({ age_sec: 51295, position_count: 0 }));
    expect(c.state).toBe('warn');
    expect(c.stateText).toBe('桥在线 · 账户停更');
    expect(c.lines.find((l) => l.k === '账户快照')?.tone).toBe('off');
  });

  it('桥健康检查报错 → 离线并带原因', () => {
    const c = tdxChannel(
      { ...HEALTHY_TDX, health: { error: 'HTTP 502' } } as never,
      summary(),
    );
    expect(c.state).toBe('off');
    expect(c.lines.find((l) => l.k === '桥服务')?.v).toContain('HTTP 502');
  });

  it('未配置桥 → 未配置（不放行下单）', () => {
    const c = tdxChannel({ enabled: false } as never, undefined);
    expect(c.state).toBe('off');
    expect(c.stateText).toBe('未配置');
    expect(c.lines.find((l) => l.k === '下单权限')?.tone).toBe('warn');
  });

  it('桥通但客户端未登录 → 客户端那一行是 warn，不是 ok', () => {
    const c = tdxChannel(
      { ...HEALTHY_TDX, health: { status: 'ok', tdx_connected: false } } as never,
      summary(),
    );
    expect(c.state).toBe('warn');
    expect(c.lines.find((l) => l.k === '通达信客户端')?.tone).toBe('warn');
  });
});

describe('qmtChannel', () => {
  const qmtSum = summary({
    account: 'qmt',
    account_id: 'qmt-default-00000001',
    label: '迅投 QMT',
    total_asset: 23850348.06,
    position_count: 50,
    age_sec: 22,
  });

  it('探针通 + 快照新鲜 → 在线（只读）且资产按千万显示', () => {
    const c = qmtChannel(qmtSum, { ok: true });
    expect(c.state).toBe('ok');
    expect(c.stateText).toBe('在线（只读）');
    expect(c.lines.find((l) => l.k === '账户资产')?.v).toBe('¥23,850,348');
  });

  it('本系统不下单这件事必须显式写出来（不谎报可下单）', () => {
    const c = qmtChannel(qmtSum, { ok: true });
    expect(c.lines.find((l) => l.k === '本系统接线')?.v).toContain('只读');
  });

  it('探针失败 → 离线', () => {
    const c = qmtChannel(qmtSum, { ok: false, error: 'Redis 超时' });
    expect(c.state).toBe('off');
    expect(c.lines.find((l) => l.k === '桥服务')?.v).toContain('Redis 超时');
  });

  // 2026-09-11 实录：Windows 侧 rpc_allow_order_methods 已是 true，
  // 界面还只写「未接入（只读）」会把「本系统没接线」说成「通道不支持下单」——排查会走错方向。
  it('Windows 侧已放开下单 → 与「本系统未接线」分两行说', () => {
    const c = qmtChannel(qmtSum, { ok: true }, { allow_order_methods: true });
    expect(c.lines.find((l) => l.k === 'Windows 侧下单闸')?.v).toBe('已放开');
    expect(c.lines.find((l) => l.k === '本系统接线')?.v).toContain('未接入');
  });

  it('Windows 侧未放开 → 下单闸显示未放开', () => {
    const c = qmtChannel(qmtSum, { ok: true }, { allow_order_methods: false });
    expect(c.lines.find((l) => l.k === 'Windows 侧下单闸')?.v).toBe('未放开');
  });

  it('桥状态没取到 → 下单闸不猜，写未知', () => {
    const c = qmtChannel(qmtSum, { ok: true }, null);
    expect(c.lines.find((l) => l.k === 'Windows 侧下单闸')?.v).toBe('未知');
  });

  it('取到桥状态时补出 RPC 版本与账号类型', () => {
    const c = qmtChannel(qmtSum, { ok: true }, {
      allow_order_methods: false,
      version: '0.3.31',
      account_type: 'STOCK',
    });
    expect(c.lines.find((l) => l.k === '桥版本')?.v).toContain('0.3.31');
    expect(c.lines.find((l) => l.k === '账号类型')?.v).toContain('STOCK');
  });
});

describe('futuChannel / ibkrChannel', () => {
  it('富途无账户 → 离线', () => {
    expect(futuChannel(null).state).toBe('off');
    expect(futuChannel({ real: null, simulate: null }).state).toBe('off');
  });

  it('富途实盘读通 → 在线', () => {
    const c = futuChannel({ real: { asset: 123456, positions: [] }, simulate: null });
    expect(c.state).toBe('ok');
    expect(c.lines.find((l) => l.k === '模拟环境')?.tone).toBe('off');
  });

  it('IBKR 无账户 → 离线（美股市值恒为 $）', () => {
    expect(ibkrChannel(null).stateText).toBe('离线');
    expect(ibkrChannel({ total_asset: 25000 }).lines[0].v).toBe('$25,000');
  });
});

describe('channelsOfMarket', () => {
  it('A 股恒为两条通道（通达信 + QMT），顺序固定', () => {
    const chans = channelsOfMarket('cn', {
      tdx: HEALTHY_TDX as never,
      qmtProbe: { ok: true },
      summaries: [summary(), summary({ account: 'qmt', account_id: 'qmt-default-00000001' })],
    });
    expect(chans.map((c) => c.label)).toEqual(['通达信桥', '迅投 QMT']);
  });

  it('A 股无摘要数据也不炸（返回两条 unknown）', () => {
    const chans = channelsOfMarket('cn', {
      tdx: null,
      qmtProbe: null,
      summaries: null,
    });
    expect(chans).toHaveLength(2);
    expect(chans[0].stateText).toBe('未配置');
    expect(chans[1].state).toBe('unknown');
  });
});
