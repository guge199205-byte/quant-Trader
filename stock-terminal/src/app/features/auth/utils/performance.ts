/**
 * 认证模块性能监控工具
 */

// 性能指标接口
export interface PerformanceMetrics {
  // 页面加载性能
  pageLoad: {
    startTime: number;
    endTime: number;
    duration: number;
  };

  // API请求性能
  apiRequests: Array<{
    url: string;
    method: string;
    startTime: number;
    endTime: number;
    duration: number;
    success: boolean;
    error?: string;
  }>;

  // 组件渲染性能
  componentRender: Array<{
    name: string;
    startTime: number;
    endTime: number;
    duration: number;
  }>;

  // 内存使用情况
  memoryUsage: {
    used: number;
    total: number;
    timestamp: number;
  }[];

  // 网络状态
  networkInfo: {
    effectiveType: string;
    downlink: number;
    rtt: number;
    saveData: boolean;
  };
}

/**
 * 性能监控器类
 */
export class PerformanceMonitor {
  private static instance: PerformanceMonitor;
  private metrics: PerformanceMetrics;
  private observers: PerformanceObserver[] = [];
  private isMonitoring = false;

  private constructor() {
    this.metrics = {
      pageLoad: {
        startTime: Date.now(),
        endTime: 0,
        duration: 0,
      },
      apiRequests: [],
      componentRender: [],
      memoryUsage: [],
      networkInfo: {
        effectiveType: '',
        downlink: 0,
        rtt: 0,
        saveData: false,
      },
    };
  }

  public static getInstance(): PerformanceMonitor {
    if (!PerformanceMonitor.instance) {
      PerformanceMonitor.instance = new PerformanceMonitor();
    }
    return PerformanceMonitor.instance;
  }

  /**
   * 开始监控
   */
  public startMonitoring(): void {
    if (this.isMonitoring) return;

    this.isMonitoring = true;
    this.metrics.pageLoad.startTime = Date.now();

    // 监控API请求
    this.monitorApiRequests();

    // 监控内存使用
    this.monitorMemoryUsage();

    // 监控网络状态
    this.monitorNetworkInfo();

    // 监控页面加载完成
    this.monitorPageLoad();

    console.log('🚀 性能监控已启动');
  }

  /**
   * 停止监控
   */
  public stopMonitoring(): void {
    if (!this.isMonitoring) return;

    this.isMonitoring = false;
    this.metrics.pageLoad.endTime = Date.now();
    this.metrics.pageLoad.duration = this.metrics.pageLoad.endTime - this.metrics.pageLoad.startTime;

    // 清理观察者
    this.observers.forEach(observer => observer.disconnect());
    this.observers = [];

    console.log('⏹️ 性能监控已停止');
    console.log('📊 性能指标:', this.getMetrics());
  }

  /**
   * 记录API请求
   */
  public recordApiRequest(
    url: string,
    method: string,
    startTime: number,
    endTime: number,
    success: boolean,
    error?: string
  ): void {
    this.metrics.apiRequests.push({
      url,
      method,
      startTime,
      endTime,
      duration: endTime - startTime,
      success,
      error,
    });

    // 保持最近100条记录
    if (this.metrics.apiRequests.length > 100) {
      this.metrics.apiRequests = this.metrics.apiRequests.slice(-100);
    }
  }

  /**
   * 记录组件渲染时间
   */
  public recordComponentRender(name: string, startTime: number, endTime: number): void {
    this.metrics.componentRender.push({
      name,
      startTime,
      endTime,
      duration: endTime - startTime,
    });

    // 保持最近50条记录
    if (this.metrics.componentRender.length > 50) {
      this.metrics.componentRender = this.metrics.componentRender.slice(-50);
    }
  }

  /**
   * 获取性能指标
   */
  public getMetrics(): PerformanceMetrics {
    return { ...this.metrics };
  }

  /**
   * 获取API请求统计
   */
  public getApiStats() {
    const requests = this.metrics.apiRequests;
    const total = requests.length;
    const successful = requests.filter(r => r.success).length;
    const failed = total - successful;

    const durations = requests.map(r => r.duration);
    const avgDuration = durations.length > 0 ? durations.reduce((a, b) => a + b, 0) / durations.length : 0;
    const maxDuration = Math.max(...durations, 0);
    const minDuration = Math.min(...durations, 0);

    return {
      total,
      successful,
      failed,
      successRate: total > 0 ? (successful / total) * 100 : 0,
      avgDuration: Math.round(avgDuration),
      maxDuration,
      minDuration,
    };
  }

  /**
   * 获取内存使用统计
   */
  public getMemoryStats() {
    const usage = this.metrics.memoryUsage;
    if (usage.length === 0) return null;

    const latest = usage[usage.length - 1];
    const usagePercent = (latest.used / latest.total) * 100;

    return {
      used: latest.used,
      total: latest.total,
      usagePercent: Math.round(usagePercent),
      timestamp: latest.timestamp,
    };
  }

  /**
   * 监控API请求
   */
  private monitorApiRequests(): void {
    // 拦截fetch请求
    const originalFetch = window.fetch;
    const self = this;

    window.fetch = async function(input: RequestInfo, init?: RequestInit) {
      const url = typeof input === 'string' ? input : ((input as any).url ?? String(input));
      const method = init?.method || 'GET';
      const startTime = Date.now();

      try {
        const response = await originalFetch.call(this, input, init);
        const endTime = Date.now();

        self.recordApiRequest(url, method, startTime, endTime, response.ok);

        return response;
      } catch (error) {
        const endTime = Date.now();
        const errMsg = (error as any)?.message ?? String(error);
        self.recordApiRequest(url, method, startTime, endTime, false, errMsg);
        throw error;
      }
    };
  }

  /**
   * 监控内存使用
   */
  private monitorMemoryUsage(): void {
    if ('memory' in performance) {
      const memory = (performance as any).memory;
      const recordUsage = () => {
        this.metrics.memoryUsage.push({
          used: memory.usedJSHeapSize,
          total: memory.totalJSHeapSize,
          timestamp: Date.now(),
        });

        // 保持最近20条记录
        if (this.metrics.memoryUsage.length > 20) {
          this.metrics.memoryUsage = this.metrics.memoryUsage.slice(-20);
        }
      };

      // 每5秒记录一次
      const interval = setInterval(recordUsage, 5000);

      // 页面卸载时清理
      window.addEventListener('beforeunload', () => {
        clearInterval(interval);
      });
    }
  }

  /**
   * 监控网络信息
   */
  private monitorNetworkInfo(): void {
    if ('connection' in navigator) {
      const connection = (navigator as any).connection;
      this.metrics.networkInfo = {
        effectiveType: connection.effectiveType,
        downlink: connection.downlink,
        rtt: connection.rtt,
        saveData: connection.saveData,
      };

      // 监听网络变化
      connection.addEventListener('change', () => {
        this.metrics.networkInfo = {
          effectiveType: connection.effectiveType,
          downlink: connection.downlink,
          rtt: connection.rtt,
          saveData: connection.saveData,
        };
      });
    }
  }

  /**
   * 监控页面加载
   */
  private monitorPageLoad(): void {
    if ('PerformanceObserver' in window) {
      const observer = new PerformanceObserver((list) => {
        const entries = list.getEntries();
        const navigationEntry = entries.find(
          entry => entry.entryType === 'navigation'
        ) as PerformanceNavigationTiming;

        if (navigationEntry) {
          this.metrics.pageLoad.endTime = navigationEntry.loadEventEnd;
          this.metrics.pageLoad.duration = navigationEntry.loadEventEnd - navigationEntry.fetchStart;
        }
      });

      observer.observe({ entryTypes: ['navigation'] });
      this.observers.push(observer);
    }
  }

  /**
   * 生成性能报告
   */
  public generateReport(): string {
    const metrics = this.getMetrics();
    const apiStats = this.getApiStats();
    const memoryStats = this.getMemoryStats();

    return `
📊 认证模块性能报告

📱 页面加载性能:
  - 加载时间: ${metrics.pageLoad.duration}ms
  - 开始时间: ${new Date(metrics.pageLoad.startTime).toLocaleString()}
  - 结束时间: ${new Date(metrics.pageLoad.endTime).toLocaleString()}

🌐 API请求统计:
  - 总请求数: ${apiStats.total}
  - 成功请求: ${apiStats.successful}
  - 失败请求: ${apiStats.failed}
  - 成功率: ${apiStats.successRate.toFixed(2)}%
  - 平均响应时间: ${apiStats.avgDuration}ms
  - 最大响应时间: ${apiStats.maxDuration}ms
  - 最小响应时间: ${apiStats.minDuration}ms

🧠 内存使用情况:
  ${memoryStats ? `- 已使用: ${(memoryStats.used / 1024 / 1024).toFixed(2)}MB
  - 总内存: ${(memoryStats.total / 1024 / 1024).toFixed(2)}MB
  - 使用率: ${memoryStats.usagePercent}%` : '内存信息不可用'}

📶 网络状态:
  - 有效类型: ${metrics.networkInfo.effectiveType}
  - 下载速度: ${metrics.networkInfo.downlink}Mbps
  - 往返时间: ${metrics.networkInfo.rtt}ms
  - 节省数据模式: ${metrics.networkInfo.saveData ? '是' : '否'}

🎯 组件渲染统计:
  - 组件数量: ${metrics.componentRender.length}
  ${metrics.componentRender.length > 0 ?
    `- 平均渲染时间: ${Math.round(metrics.componentRender.reduce((sum, c) => sum + c.duration, 0) / metrics.componentRender.length)}ms` :
    '- 暂无组件渲染记录'}
    `;
  }
}

// 导出单例实例
export const performanceMonitor = PerformanceMonitor.getInstance();

// 便捷的性能监控Hook
export const usePerformanceMonitor = () => {
  const startMonitoring = () => {
    performanceMonitor.startMonitoring();
    return () => performanceMonitor.stopMonitoring();
  };

  const recordComponentRender = (name: string) => {
    const startTime = Date.now();
    return () => {
      const endTime = Date.now();
      performanceMonitor.recordComponentRender(name, startTime, endTime);
    };
  };

  const getMetrics = () => performanceMonitor.getMetrics();
  const getReport = () => performanceMonitor.generateReport();

  return {
    startMonitoring,
    recordComponentRender,
    getMetrics,
    getReport,
  };
};

export default {
  PerformanceMonitor,
  performanceMonitor,
  usePerformanceMonitor,
};
