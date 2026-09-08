/** K线图：lightweight-charts（TradingView 官方开源图表库）封装。
 *
 *  - 蜡烛图 + 成交量副图；A股红涨绿跌口径
 *  - 回测成交点用 markers 标注（买↑ / 卖↓）
 *  - 容器尺寸变化自适应（ResizeObserver），卸载时销毁实例
 */
import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { BtTrade, Kline } from '../api/client';

const UP = '#e2373b';    // A股：红涨
const DOWN = '#1a9e5c';  // 绿跌

/** 'YYYY-MM-DD' → lightweight-charts 的 Time */
const toTime = (d: string) => d as unknown as Time;

export default function KLineChart({
  bars,
  trades = [],
  height = 420,
  showMarkers = true,
}: {
  bars: Kline[];
  trades?: BtTrade[];
  height?: number;
  showMarkers?: boolean;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const marksRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  // 建图（只一次）
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const chart = createChart(el, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: '#ffffff' },
        textColor: '#1a1a1a',
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#f0f0f0' },
        horzLines: { color: '#f0f0f0' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#000000' },
      timeScale: {
        borderColor: '#000000',
        rightOffset: 4,
        barSpacing: 6,
        // 默认最小 0.5px/根：2595 根全历史在 ~1080px 宽里塞不下（只能看到后 2100 根），
        // 放宽到 0.2 让「全历史」真的全览（缩略图观感，放大后正常）
        minBarSpacing: 0.2,
      },
      localization: {
        locale: 'zh-CN',
        priceFormatter: (p: number) => p.toFixed(2),
      },
    });
    chartRef.current = chart;

    candleRef.current = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderUpColor: UP,
      borderDownColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      priceLineVisible: false,
    });
    volRef.current = chart.addSeries(HistogramSeries, {
      priceScaleId: 'vol',
      priceFormat: { type: 'volume' },
      priceLineVisible: false,
      lastValueVisible: false,
    });
    chart.priceScale('vol').applyOptions({
      scaleMargins: { top: 0.78, bottom: 0 },
      borderColor: '#e0e0e0',
    });
    // v5：markers 走插件（v4 的 series.setMarkers 已移除）
    marksRef.current = createSeriesMarkers(candleRef.current, []);

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: el.clientWidth });
    });
    ro.observe(el);
    chart.applyOptions({ width: el.clientWidth });

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volRef.current = null;
      marksRef.current = null;
    };
  }, [height]);

  // 灌数据
  useEffect(() => {
    const chart = chartRef.current;
    const candle = candleRef.current;
    const vol = volRef.current;
    if (!chart || !candle || !vol) return;

    candle.setData(
      bars.map((b) => ({
        time: toTime(b.date),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      })),
    );
    vol.setData(
      bars.map((b) => ({
        time: toTime(b.date),
        value: b.volume,
        color: b.close >= b.open ? 'rgba(226,55,59,0.35)' : 'rgba(26,158,92,0.35)',
      })),
    );

    if (showMarkers && trades.length) {
      const marks: SeriesMarker<Time>[] = [];
      for (const t of trades) {
        if (t.entry_time) {
          marks.push({
            time: t.entry_time.slice(0, 10) as unknown as UTCTimestamp,
            position: 'belowBar',
            color: UP,
            shape: 'arrowUp',
            text: '买',
          });
        }
        if (t.exit_time) {
          marks.push({
            time: t.exit_time.slice(0, 10) as unknown as UTCTimestamp,
            position: 'aboveBar',
            color: DOWN,
            shape: 'arrowDown',
            text: '卖',
          });
        }
      }
      marks.sort((a, b) => (a.time as string).localeCompare(b.time as string));
      marksRef.current?.setMarkers(marks);
    } else {
      marksRef.current?.setMarkers([]);
    }
    chart.timeScale().fitContent();
  }, [bars, trades, showMarkers]);

  return <div ref={boxRef} className="kline-box" style={{ height }} />;
}
