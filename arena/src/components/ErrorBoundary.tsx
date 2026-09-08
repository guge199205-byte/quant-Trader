import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  err: Error | null;
}

/** 顶层渲染错误兜底：任何页面 render 抛错显示可重载提示，不再整站白屏。
 *  （2026-09-08 白屏事故：净值降采样下标越界在 render 路径抛 TypeError，
 *  无边界时 React 卸载整树 → 纯白页面。） */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { err: null };

  static getDerivedStateFromError(err: Error): State {
    return { err };
  }

  render() {
    if (!this.state.err) return this.props.children;
    return (
      <div className="error-box">
        页面渲染出错：{this.state.err.message}
        <br />
        <br />
        <button className="time-btn active" onClick={() => window.location.reload()}>
          刷新页面
        </button>
      </div>
    );
  }
}
