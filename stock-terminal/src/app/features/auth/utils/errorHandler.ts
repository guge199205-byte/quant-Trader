/**
 * 错误处理工具
 * 统一处理认证相关错误
 */

import { message, notification } from 'antd';
import { createElement } from 'react';
import type { ReactNode } from 'react';

// 错误类型枚举
export enum ErrorType {
  VALIDATION = 'validation',
  NETWORK = 'network',
  AUTHENTICATION = 'authentication',
  AUTHORIZATION = 'authorization',
  SERVER = 'server',
  UNKNOWN = 'unknown'
}

// 错误级别枚举
export enum ErrorLevel {
  INFO = 'info',
  WARNING = 'warning',
  ERROR = 'error',
  SUCCESS = 'success'
}

// 标准化错误接口
export interface StandardError {
  type: ErrorType;
  level: ErrorLevel;
  message: string;
  details?: string;
  code?: string | number;
  timestamp: number;
  context?: Record<string, any>;
}

/**
 * 错误处理器类
 */
export class ErrorHandler {
  private static instance: ErrorHandler;
  private errorQueue: StandardError[] = [];
  private maxQueueSize = 100;

  private constructor() {}

  public static getInstance(): ErrorHandler {
    if (!ErrorHandler.instance) {
      ErrorHandler.instance = new ErrorHandler();
    }
    return ErrorHandler.instance;
  }

  /**
   * 解析错误信息
   */
  public parseError(error: any): StandardError {
    const timestamp = Date.now();

    // 如果已经是标准化错误
    if (error && error.type && error.level && error.message) {
      return error as StandardError;
    }

    // 解析HTTP状态码错误（优先处理存在响应的情况）
    if (error.response) {
      const status = error.response.status;
      const data = error.response.data || {};
      const msg =
        data.message ||
        data.detail ||
        data.error ||
        error.response.statusText ||
        this.getDefaultMessageByStatus(status);

      return {
        type: this.getErrorTypeByStatus(status),
        level: this.getErrorLevelByStatus(status),
        message: msg,
        details: data.details || data.detail || error.message,
        code: data.error_code || data.code || status,
        timestamp,
        context: { url: error.config?.url, method: error.config?.method }
      };
    }

    // 解析网络错误（无响应时才视为网络问题）
    if (error.name === 'AxiosError' || error.code === 'NETWORK_ERROR') {
      return {
        type: ErrorType.NETWORK,
        level: ErrorLevel.ERROR,
        message: '网络连接失败，请检查网络设置',
        details: error.message,
        code: error.code,
        timestamp,
        context: { url: error.config?.url, method: error.config?.method }
      };
    }

    // 解析认证相关错误
    if (error.message?.includes('token') || error.message?.includes('认证')) {
      return {
        type: ErrorType.AUTHENTICATION,
        level: ErrorLevel.ERROR,
        message: '认证失败，请重新登录',
        details: error.message,
        timestamp
      };
    }

    if (error.message?.includes('权限') || error.message?.includes('授权')) {
      return {
        type: ErrorType.AUTHORIZATION,
        level: ErrorLevel.ERROR,
        message: '权限不足，无法执行此操作',
        details: error.message,
        timestamp
      };
    }

    // 解析验证错误
    if (error.message?.includes('验证') || error.message?.includes('格式')) {
      return {
        type: ErrorType.VALIDATION,
        level: ErrorLevel.WARNING,
        message: '输入信息格式不正确',
        details: error.message,
        timestamp
      };
    }

    // 默认未知错误
    return {
      type: ErrorType.UNKNOWN,
      level: ErrorLevel.ERROR,
      message: error.message || '未知错误',
      details: error.stack,
      timestamp
    };
  }

  /**
   * 根据HTTP状态码获取错误类型
   */
  private getErrorTypeByStatus(status: number): ErrorType {
    if (status === 400 || status === 422) return ErrorType.VALIDATION;
    if (status === 401) return ErrorType.AUTHENTICATION;
    if (status === 403) return ErrorType.AUTHORIZATION;
    if (status >= 500) return ErrorType.SERVER;
    return ErrorType.UNKNOWN;
  }

  /**
   * 根据HTTP状态码获取错误级别
   */
  private getErrorLevelByStatus(status: number): ErrorLevel {
    if (status === 400 || status === 422) return ErrorLevel.WARNING;
    if (status >= 400) return ErrorLevel.ERROR;
    return ErrorLevel.ERROR;
  }

  /**
   * 根据HTTP状态码获取默认消息
   */
  private getDefaultMessageByStatus(status: number): string {
    const messages: Record<number, string> = {
      400: '请求参数错误',
      401: '身份验证失败',
      403: '权限不足',
      404: '资源不存在',
      422: '数据验证失败',
      429: '请求过于频繁，请稍后再试',
      500: '服务器内部错误',
      502: '网关错误',
      503: '服务暂时不可用',
      504: '网关超时'
    };

    return messages[status] || '请求失败';
  }

  /**
   * 处理错误并显示用户友好的提示
   */
  public handleError(error: any, options: {
    showNotification?: boolean;
    showMessage?: boolean;
    duration?: number;
    context?: string;
  } = {}): StandardError {
    const {
      showNotification = true,
      showMessage = false,
      duration = 3,
      context = ''
    } = options;

    const standardError = this.parseError(error);

    // 添加上下文信息
    if (context) {
      standardError.context = { ...standardError.context, context };
    }

    // 记录错误到队列
    this.addToQueue(standardError);

    // 根据错误级别选择显示方式
    if (showNotification) {
      this.showNotification(standardError, duration);
    } else if (showMessage) {
      this.showMessage(standardError, duration);
    }

    // 记录到控制台（开发环境）
    if (process.env.NODE_ENV === 'development') {
      console.error('🚨 Auth Error:', standardError);
    }

    return standardError;
  }

  /**
   * 显示通知
   */
  private showNotification(error: StandardError, duration: number): void {
    const config = {
      message: this.getTitleByLevel(error.level),
      description: this.buildDescription(error),
      duration,
      placement: 'topRight' as const
    };

    switch (error.level) {
      case ErrorLevel.SUCCESS:
        notification.success(config);
        break;
      case ErrorLevel.WARNING:
        notification.warning(config);
        break;
      case ErrorLevel.ERROR:
        notification.error(config);
        break;
      default:
        notification.info(config);
    }
  }

  /**
   * 创建包含代码与详情的描述内容
   */
  private buildDescription(error: StandardError): ReactNode {
    const descriptionElements: ReactNode[] = [
      createElement('div', { key: 'message' }, error.message)
    ];

    if (error.code) {
      descriptionElements.push(
        createElement(
          'div',
          { key: 'code', style: { marginTop: 4, fontSize: 12, color: '#595959' } },
          `错误代码: ${error.code}`
        )
      );
    }

    if (error.details && error.details !== error.message) {
      descriptionElements.push(
        createElement(
          'div',
          { key: 'details', style: { marginTop: 4, fontSize: 12, color: '#8c8c8c' } },
          `详情: ${error.details}`
        )
      );
    }

    return createElement('div', null, descriptionElements);
  }

  /**
   * 显示消息
   */
  private showMessage(error: StandardError, duration: number): void {
    const config = {
      content: error.message,
      duration: duration
    };

    switch (error.level) {
      case ErrorLevel.SUCCESS:
        message.success(config);
        break;
      case ErrorLevel.WARNING:
        message.warning(config);
        break;
      case ErrorLevel.ERROR:
        message.error(config);
        break;
      default:
        message.info(config);
    }
  }

  /**
   * 根据错误级别获取标题
   */
  private getTitleByLevel(level: ErrorLevel): string {
    const titles = {
      [ErrorLevel.SUCCESS]: '成功',
      [ErrorLevel.WARNING]: '警告',
      [ErrorLevel.ERROR]: '错误',
      [ErrorLevel.INFO]: '提示'
    };

    return titles[level] || '提示';
  }

  /**
   * 添加错误到队列
   */
  private addToQueue(error: StandardError): void {
    this.errorQueue.unshift(error);
    if (this.errorQueue.length > this.maxQueueSize) {
      this.errorQueue = this.errorQueue.slice(0, this.maxQueueSize);
    }
  }

  /**
   * 获取错误队列
   */
  public getErrorQueue(): StandardError[] {
    return [...this.errorQueue];
  }

  /**
   * 清空错误队列
   */
  public clearErrorQueue(): void {
    this.errorQueue = [];
  }

  /**
   * 获取最近的错误
   */
  public getLastError(): StandardError | null {
    return this.errorQueue.length > 0 ? this.errorQueue[0] : null;
  }

  /**
   * 获取特定类型的错误
   */
  public getErrorsByType(type: ErrorType): StandardError[] {
    return this.errorQueue.filter(error => error.type === type);
  }

  /**
   * 获取特定级别的错误
   */
  public getErrorsByLevel(level: ErrorLevel): StandardError[] {
    return this.errorQueue.filter(error => error.level === level);
  }
}

// 导出单例实例
export const errorHandler = ErrorHandler.getInstance();

// 便捷的错误处理函数
export const handleError = (
  error: any,
  options?: {
    showNotification?: boolean;
    showMessage?: boolean;
    duration?: number;
    context?: string;
  }
): StandardError => {
  return errorHandler.handleError(error, options);
};

// 认证专用错误处理函数
export const handleAuthError = (error: any, context?: string): StandardError => {
  return handleError(error, {
    showNotification: true,
    duration: 3,
    context: context || 'authentication'
  });
};

// 网络错误处理函数
export const handleNetworkError = (error: any, context?: string): StandardError => {
  return handleError(error, {
    showNotification: true,
    duration: 5,
    context: context || 'network'
  });
};

// 验证错误处理函数
export const handleValidationError = (error: any, context?: string): StandardError => {
  return handleError(error, {
    showMessage: true,
    duration: 2,
    context: context || 'validation'
  });
};

// 服务器错误处理函数
export const handleServerError = (error: any, context?: string): StandardError => {
  return handleError(error, {
    showNotification: true,
    duration: 4,
    context: context || 'server'
  });
};

export default {
  ErrorHandler,
  errorHandler,
  handleError,
  handleAuthError,
  handleNetworkError,
  handleValidationError,
  handleServerError,
  ErrorType,
  ErrorLevel
};
