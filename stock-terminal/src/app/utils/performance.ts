/**
 * 前端性能优化工具
 */

/**
 * 防抖函数
 * @param func 要执行的函数
 * @param wait 等待时间（毫秒）
 * @returns 防抖后的函数
 */
export function debounce<T extends (...args: any[]) => any>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timeout: NodeJS.Timeout | null = null;

  return function(this: any, ...args: Parameters<T>) {
    const context = this;

    if (timeout) {
      clearTimeout(timeout);
    }

    timeout = setTimeout(() => {
      func.apply(context, args);
    }, wait);
  };
}

/**
 * 节流函数
 * @param func 要执行的函数
 * @param wait 等待时间（毫秒）
 * @returns 节流后的函数
 */
export function throttle<T extends (...args: any[]) => any>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let inThrottle: boolean = false;
  let lastResult: any;

  return function(this: any, ...args: Parameters<T>) {
    const context = this;

    if (!inThrottle) {
      lastResult = func.apply(context, args);
      inThrottle = true;

      setTimeout(() => {
        inThrottle = false;
      }, wait);
    }

    return lastResult;
  };
}

/**
 * 性能监控装饰器
 * @param target 目标对象
 * @param propertyKey 方法名
 * @param descriptor 方法描述符
 */
export function measurePerformance(
  target: any,
  propertyKey: string,
  descriptor: PropertyDescriptor
) {
  const originalMethod = descriptor.value;

  descriptor.value = async function(...args: any[]) {
    const start = performance.now();
    const result = await originalMethod.apply(this, args);
    const end = performance.now();
    const duration = end - start;

    if (duration > 100) {
      console.warn(
        `[性能警告] ${propertyKey} 执行时间: ${duration.toFixed(2)}ms`
      );
    } else {
      console.debug(
        `[性能] ${propertyKey} 执行时间: ${duration.toFixed(2)}ms`
      );
    }

    return result;
  };

  return descriptor;
}

/**
 * 延迟加载图片
 * @param imageElement 图片元素
 */
export function lazyLoadImage(imageElement: HTMLImageElement) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const img = entry.target as HTMLImageElement;
        const src = img.dataset.src;

        if (src) {
          img.src = src;
          img.removeAttribute('data-src');
          observer.unobserve(img);
        }
      }
    });
  });

  observer.observe(imageElement);
}

/**
 * 批量处理任务
 * @param items 要处理的项目列表
 * @param batchSize 每批处理的数量
 * @param processor 处理函数
 * @returns 处理结果
 */
export async function batchProcess<T, R>(
  items: T[],
  batchSize: number,
  processor: (batch: T[]) => Promise<R[]>
): Promise<R[]> {
  const results: R[] = [];

  for (let i = 0; i < items.length; i += batchSize) {
    const batch = items.slice(i, i + batchSize);
    const batchResults = await processor(batch);
    results.push(...batchResults);

    // 给浏览器一点时间处理其他任务
    await new Promise(resolve => setTimeout(resolve, 0));
  }

  return results;
}

/**
 * 内存优化：清理未使用的数据
 */
export function cleanupUnusedData() {
  // 清理过期的缓存
  if ('caches' in window) {
    caches.keys().then(names => {
      names.forEach(name => {
        if (name.includes('old') || name.includes('temp')) {
          caches.delete(name);
        }
      });
    });
  }

  // 分批清理 sessionStorage 中的临时数据，避免长时间阻塞主线程
  const keys = Object.keys(sessionStorage);
  const batchSize = 50;

  const processBatch = (startIndex: number) => {
    const endIndex = Math.min(startIndex + batchSize, keys.length);

    for (let i = startIndex; i < endIndex; i++) {
      const key = keys[i];

      if (key.startsWith('temp_') || key.startsWith('cache_')) {
        const item = sessionStorage.getItem(key);
        if (item) {
          try {
            const data = JSON.parse(item);
            if (data.expires && data.expires < Date.now()) {
              sessionStorage.removeItem(key);
            }
          } catch (e) {
            // 无效的数据，删除
            sessionStorage.removeItem(key);
          }
        }
      }
    }

    if (endIndex < keys.length) {
      // 让出主线程，下一批稍后再处理
      setTimeout(() => processBatch(endIndex), 0);
    }
  };

  if (keys.length > 0) {
    // 将实际清理工作放到后续事件循环中执行
    setTimeout(() => processBatch(0), 0);
  }
}

/**
 * 请求去重
 * 防止相同请求同时发起多次
 */
class RequestDeduplicator {
  private pendingRequests: Map<string, Promise<any>> = new Map();

  /**
   * 执行请求（自动去重）
   * @param key 请求唯一标识
   * @param requestFunc 请求函数
   * @returns 请求结果
   */
  async execute<T>(
    key: string,
    requestFunc: () => Promise<T>
  ): Promise<T> {
    // 如果已有相同请求在进行中，返回该请求的 Promise
    if (this.pendingRequests.has(key)) {
      console.debug(`[请求去重] 使用缓存的请求: ${key}`);
      return this.pendingRequests.get(key)!;
    }

    // 创建新请求
    const promise = requestFunc().finally(() => {
      // 请求完成后，从待处理列表中移除
      this.pendingRequests.delete(key);
    });

    this.pendingRequests.set(key, promise);
    return promise;
  }

  /**
   * 取消所有待处理的请求
   */
  clear() {
    this.pendingRequests.clear();
  }
}

export const requestDeduplicator = new RequestDeduplicator();

/**
 * 虚拟滚动辅助工具
 */
export class VirtualScrollHelper {
  private container: HTMLElement;
  private itemHeight: number;
  private bufferSize: number;

  constructor(
    container: HTMLElement,
    itemHeight: number,
    bufferSize: number = 5
  ) {
    this.container = container;
    this.itemHeight = itemHeight;
    this.bufferSize = bufferSize;
  }

  /**
   * 计算可见项的范围
   * @param totalItems 总项数
   * @returns 可见范围 {start, end}
   */
  getVisibleRange(totalItems: number): { start: number; end: number } {
    const scrollTop = this.container.scrollTop;
    const containerHeight = this.container.clientHeight;

    const start = Math.max(
      0,
      Math.floor(scrollTop / this.itemHeight) - this.bufferSize
    );

    const visibleCount = Math.ceil(containerHeight / this.itemHeight);
    const end = Math.min(
      totalItems,
      start + visibleCount + this.bufferSize * 2
    );

    return { start, end };
  }

  /**
   * 获取偏移量
   * @param start 起始索引
   * @returns 偏移量（像素）
   */
  getOffset(start: number): number {
    return start * this.itemHeight;
  }

  /**
   * 获取总高度
   * @param totalItems 总项数
   * @returns 总高度（像素）
   */
  getTotalHeight(totalItems: number): number {
    return totalItems * this.itemHeight;
  }
}

/**
 * 组件性能监控
 */
export class ComponentPerformanceMonitor {
  private metrics: Map<string, { count: number; totalTime: number }> = new Map();

  /**
   * 记录组件渲染时间
   * @param componentName 组件名称
   * @param duration 渲染时间（毫秒）
   */
  recordRender(componentName: string, duration: number) {
    if (!this.metrics.has(componentName)) {
      this.metrics.set(componentName, { count: 0, totalTime: 0 });
    }

    const metric = this.metrics.get(componentName)!;
    metric.count++;
    metric.totalTime += duration;

    // 警告渲染时间过长的组件
    if (duration > 100) {
      console.warn(
        `[性能警告] ${componentName} 渲染时间过长: ${duration.toFixed(2)}ms`
      );
    }
  }

  /**
   * 获取性能统计
   * @returns 性能统计数据
   */
  getStats() {
    const stats: Record<string, any> = {};

    this.metrics.forEach((metric, componentName) => {
      stats[componentName] = {
        count: metric.count,
        avgTime: metric.totalTime / metric.count,
        totalTime: metric.totalTime
      };
    });

    return stats;
  }

  /**
   * 重置统计
   */
  reset() {
    this.metrics.clear();
  }
}

export const componentMonitor = new ComponentPerformanceMonitor();
