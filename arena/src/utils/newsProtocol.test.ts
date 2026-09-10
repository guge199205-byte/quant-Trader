import { describe, expect, it } from 'vitest';
import {
  PROTO_SPECS,
  isGateAgent,
  parseGate,
  parseProto,
  parseProtoLine,
  protoSpecOf,
  protoSummary,
} from './newsProtocol';

// 真实样本（取自 data/agent_data_astock/news-chief/log/2026-09-10/log.jsonl）
const CHIEF = [
  'T | 宏观政策托底 | 证监会提高制度包容性、严格规范控股股东实控人减持→金融权重受益',
  'T | 苹果折叠屏产业链 | 首款折叠屏iPhone Duo亮相→供应链放量',
  'W | 000958.SZ | 电投产融 | 金融强国规划→产融平台受益 | 金融权重放量走强',
  'R | 油价破百、油市地缘风险再起，输入型通胀挤压中下游盈利',
  'H | 301109.SZ | 军信股份 | 利好 | 1 | 短期 | 其他 | 华源证券维持买入评级 | 格隆汇快讯 | 固废出海获券商再背书',
  'N | 关注中小市值主题类联动；盐化工链成本端同步，勿双押',
  'E | 政策托底与中美磋商改善风险偏好，但油价破百压制小盘主题',
  'C | 0.64',
].join('\n');

const HOLDINGS = [
  'H | 601158.SH | 重庆水务 | 利好 | 1 | 政策监管：淡化数量型中介目标→高股息公用事业强化 | 板块传导',
  'X | 002521.SZ | 齐峰新材：与603551.SH同属中小市值主题，易同步回撤',
  'A | 观察：油价链利好与成本承压并存，勿双押',
  'C | 0.6',
].join('\n');

const GATE = ['23 skip', '12 macro', '4 holdings', '33 skip'].join('\n');

describe('parseProtoLine', () => {
  it('按主字段表拆出带标签的单元格', () => {
    // Arrange
    const spec = PROTO_SPECS['news-chief'];

    // Act
    const row = parseProtoLine('H | 301109.SZ | 军信股份 | 利好 | 1 | 短期 | 其他 | 标题 | 来源 | 备注', spec);

    // Assert
    expect(row).not.toBeNull();
    expect(row!.token).toBe('H');
    expect(row!.label).toBe('持仓转写');
    expect(row!.cells.map((c) => c.k)).toEqual([
      '代码',
      '名称',
      '判定',
      '力度',
      '周期',
      '事件',
      '标题',
      '来源',
      '备注',
    ]);
    expect(row!.cells[0].v).toBe('301109.SZ');
    expect(row!.cells[2].v).toBe('利好');
    expect(row!.cells[2].kind).toBe('sent');
  });

  it('缺列时留空不报错（模型少写一列是常态）', () => {
    const row = parseProtoLine('W | 000958.SZ | 电投产融 | 逻辑', PROTO_SPECS['news-chief']);
    expect(row!.cells[3].v).toBe('');
  });

  it('多写的列并进最后一个单元格，不静默丢数据', () => {
    const row = parseProtoLine(
      'V | 中性偏多 | 0.2 | 额外说明',
      PROTO_SPECS['news-macro'],
    );
    expect(row!.cells[1].v).toBe('0.2 · 额外说明');
  });

  it('token 不在字段表内时返回 null（走散文回落）', () => {
    expect(parseProtoLine('Z | 未知行 | x', PROTO_SPECS['news-chief'])).toBeNull();
  });

  it('没有竖线的散文行返回 null', () => {
    expect(parseProtoLine('今日大盘震荡，注意风险。', PROTO_SPECS['news-chief'])).toBeNull();
  });

  it('同一 token 在不同段落列数不同也各按各的表拆', () => {
    // H 在持仓段是 7 列（事件类型:传导链 合并成一格）
    const hold = parseProtoLine(HOLDINGS.split('\n')[0], PROTO_SPECS['news-holdings']);
    expect(hold!.cells.map((c) => c.k)).toEqual(['代码', '名称', '判定', '力度', '传导链', '来源']);
    expect(hold!.cells[4].v).toContain('政策监管');
  });
});

describe('parseProto', () => {
  it('混排时协议行进 rows、散文行进 plain', () => {
    const text = ['C | 0.64', '以上为最终结论。', 'E | 编辑备注'].join('\n');
    const { rows, plain } = parseProto(text, PROTO_SPECS['news-chief']);
    expect(rows.map((r) => r.token)).toEqual(['C', 'E']);
    expect(plain).toEqual(['以上为最终结论。']);
  });

  it('空行忽略、首尾空白不影响解析', () => {
    const { rows, plain } = parseProto('\n  C | 0.5  \n\n', PROTO_SPECS['news-chief']);
    expect(rows).toHaveLength(1);
    expect(rows[0].cells[0].v).toBe('0.5');
    expect(plain).toEqual([]);
  });

  it('主编 8 行样本全部命中字段表', () => {
    const { rows, plain } = parseProto(CHIEF, PROTO_SPECS['news-chief']);
    expect(plain).toEqual([]);
    expect(rows).toHaveLength(8);
  });
});

describe('parseGate', () => {
  it('按方向分桶并保留序号', () => {
    const g = parseGate(GATE);
    expect(g.skip.sort((a, b) => a - b)).toEqual([23, 33]);
    expect(g.macro).toEqual([12]);
    expect(g.holdings).toEqual([4]);
    expect(g.plain).toEqual([]);
  });

  it('识别 micro（默认值，等于没改）与异常行', () => {
    const g = parseGate(['1 micro', '乱七八糟一行'].join('\n'));
    expect(g.plain).toEqual(['乱七八糟一行']);
    expect(g.skip).toEqual([]);
  });

  it('isGateAgent 只认门卫', () => {
    expect(isGateAgent('news-gate')).toBe(true);
    expect(isGateAgent('news-chief')).toBe(false);
  });
});

describe('protoSummary', () => {
  it('主编：压成主题/关注/风险/持仓计数', () => {
    expect(protoSummary(CHIEF, 'news-chief')).toBe('主题 2 · 关注 1 · 风险 1 · 持仓 1 · 置信 0.64');
  });

  it('持仓段：区分利好利空', () => {
    expect(protoSummary(HOLDINGS, 'news-holdings')).toContain('利好 1 / 利空 0');
  });

  it('门卫：无剔除时明确说全保留', () => {
    expect(protoSummary('', 'news-gate')).toBe('本轮无剔除 · 全部条目保留（默认 micro）');
    expect(protoSummary(GATE, 'news-gate')).toBe('剔除 2 · 转宏观 1 · 转持仓 1');
  });

  it('散文段（晚间复盘）没有协议摘要', () => {
    expect(protoSpecOf('news-review')).toBeNull();
    expect(protoSummary('复盘完成：有用 13 / 反向 3。', 'news-review')).toBe('');
  });
});
