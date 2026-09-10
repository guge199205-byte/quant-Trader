/** QMT 桥返回 → 总控面板数据的适配器。重点防两类错：
 *  1. last_price=0 是桥「拿不到价」的约定（见 qmt_bridge._account_query），
 *     不能回落成成本价冒充现价 —— 否则面板盈亏全 0 看着像"平盘"；
 *  2. 盈亏口径：代码库全局约定 pnl_pct 是百分数（1.43 = +1.43%），不是小数。 */
import { describe, expect, test } from 'vitest';
import { api, fetchQmtAccount, reshapeQmtAccount } from './client';

const RAW = {
  account_id: '40327478',
  asset: { asset: 23850348.06, cash: 21506660.26, market_value: 2343646.3, frozen_cash: 0 },
  positions: [
    {
      stock_code: '920950.BJ',
      stock_name: '迅安科技',
      cost_price: 13.4334,
      total_volume: 40000,
      available_volume: 0,
      market_value: 531600,
      last_price: 13.29,
    },
    {
      stock_code: '600000.SH',
      stock_name: '浦发银行',
      cost_price: 9.4399,
      total_volume: 100,
      available_volume: 100,
      market_value: 935,
      last_price: 9.35,
    },
  ],
};

describe('reshapeQmtAccount', () => {
  test('映射账户汇总与持仓字段', () => {
    // Act
    const acct = reshapeQmtAccount(RAW);

    // Assert
    expect(acct.asset).toBeCloseTo(23850348.06, 2);
    expect(acct.cash).toBeCloseTo(21506660.26, 2);
    expect(acct.market_value).toBeCloseTo(2343646.3, 2);
    expect(acct.account_id).toBe('40327478');
    expect(acct.channel_used).toBe('qmt');
    expect(acct.positions).toHaveLength(2);
    expect(acct.positions[0]).toMatchObject({
      stock_code: '920950.BJ',
      name: '迅安科技',
      total_volume: 40000,
      available_volume: 0,
      last_price: 13.29,
    });
  });

  test('盈亏按百分数约定计算（1.43 表示 +1.43%）', () => {
    // Act
    const [, p] = reshapeQmtAccount(RAW).positions;

    // Assert：成本 9.4399 → 现价 9.35，约 -0.95%
    expect(p.pnl).toBeCloseTo((9.35 - 9.4399) * 100, 1);
    expect(p.pnl_pct).toBeCloseTo(-0.95, 1);
  });

  test('last_price=0（桥拿不到价）→ 盈亏留 0，不用成本价冒充现价', () => {
    // Arrange
    const raw = {
      ...RAW,
      positions: [{ ...RAW.positions[0], last_price: 0 }],
    };

    // Act
    const [p] = reshapeQmtAccount(raw).positions;

    // Assert
    expect(p.last_price).toBe(0);
    expect(p.pnl).toBe(0);
    expect(p.pnl_pct).toBe(0);
    expect(p.position_value).toBe(531600); // 市值仍用桥给的数
  });

  test('缺 market_value 时用 现价×数量 兜底；无现价则用成本', () => {
    // Arrange
    const priced = { ...RAW, positions: [{ ...RAW.positions[0], market_value: 0 }] };
    const priceless = {
      ...RAW,
      positions: [{ ...RAW.positions[0], market_value: 0, last_price: 0 }],
    };

    // Act & Assert
    expect(reshapeQmtAccount(priced).positions[0].position_value).toBeCloseTo(13.29 * 40000, 2);
    expect(reshapeQmtAccount(priceless).positions[0].position_value).toBeCloseTo(
      13.4334 * 40000, 2,
    );
  });

  test('cost_price=0（送股/字段缺失）→ 盈亏留 0；面板须按成本缺失显示 —', () => {
    // Arrange：有现价但没成本（QMT 侧 avg_price 字段缺失/送股股成本为 0）
    const raw = { ...RAW, positions: [{ ...RAW.positions[0], cost_price: 0 }] };

    // Act
    const [p] = reshapeQmtAccount(raw).positions;

    // Assert：适配器不虚报，0 值盈利的「平盘」假象由面板的成本守卫兜住
    expect(p.last_price).toBeGreaterThan(0);
    expect(p.pnl).toBe(0);
    expect(p.pnl_pct).toBe(0);
  });

  test('cost_price<0（摊薄成本）→ 绝对盈亏保留、百分比留 0（实盘 002141 真实样例）', () => {
    // Arrange：长期持仓分红累计超过原成本，QMT 的 avg_price 为负
    const raw = {
      ...RAW,
      positions: [{
        stock_code: '002141.SZ',
        stock_name: '贤丰控股',
        cost_price: -2.5142999500000003,
        total_volume: 200,
        available_volume: 200,
        market_value: 1282,
        last_price: 6.41,
      }],
    };

    // Act
    const [p] = reshapeQmtAccount(raw).positions;

    // Assert：(6.41-(-2.5143))*200 ≈ +1784.86，能算；百分比 (P-C)/C 在 C<0 时符号颠倒，不能算
    expect(p.pnl).toBeCloseTo(1784.86, 1);
    expect(p.pnl_pct).toBe(0);
  });

  test('清仓残留行（数量<=0）被过滤', () => {
    // Arrange
    const raw = { ...RAW, positions: [...RAW.positions, { ...RAW.positions[0], total_volume: 0 }] };

    // Act & Assert
    expect(reshapeQmtAccount(raw).positions).toHaveLength(2);
  });

  test('asset 为 null / positions 缺失时不炸，全 0', () => {
    // Act
    const acct = reshapeQmtAccount({ account_id: undefined, asset: null, positions: null });

    // Assert
    expect(acct.asset).toBe(0);
    expect(acct.cash).toBe(0);
    expect(acct.positions).toEqual([]);
    expect(acct.account_id).toBe('');
  });

  test('stock_name 缺失回落股票代码', () => {
    // Arrange
    const raw = { ...RAW, positions: [{ ...RAW.positions[0], stock_name: '' }] };

    // Act & Assert
    expect(reshapeQmtAccount(raw).positions[0].name).toBe('920950.BJ');
  });
});

describe('fetchQmtAccount（桥不可达时的降级路径）', () => {
  const withGet = async (resp: unknown, fn: () => Promise<void>) => {
    const orig = api.get;
    api.get = (async () => resp) as typeof api.get;
    try {
      await fn();
    } finally {
      api.get = orig;
    }
  };

  test('后端 success:false（未配置/桥断）→ null，面板走占位分支', async () => {
    await withGet({ data: { success: false, error: 'QMT 查询失败: 连接超时' } }, async () => {
      expect(await fetchQmtAccount()).toBeNull();
    });
  });

  test('success:true → 交给 reshape', async () => {
    await withGet({ data: { success: true, data: RAW } }, async () => {
      const acct = await fetchQmtAccount();
      expect(acct?.account_id).toBe('40327478');
      expect(acct?.positions).toHaveLength(2);
    });
  });
});
