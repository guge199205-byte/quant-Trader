/**
 * 统一错误处理工具
 * 处理API调用错误和业务逻辑错误
 */

export interface APIError {
  code: string;
  message: string;
  details?: any;
  status?: number;
}

export class APIErrorHandler {
  /**
   * 处理API错误，返回用户友好的错误信息
   */
  static handleError(error: any, context: string = 'API调用'): APIError {
    console.error(`${context}错误:`, error);

    // Axios错误处理
    if (error.isAxiosError) {
      const status = error.response?.status;
      const data = error.response?.data;
      const message = data?.message || error.message || '未知错误';

      switch (status) {
        case 400:
          return {
            code: 'BAD_REQUEST',
            message: this.getBadRequestMessage(message),
            details: data,
            status
          };

        case 401:
          return {
            code: 'UNAUTHORIZED',
            message: '认证失败，请检查API密钥配置',
            details: data,
            status
          };

        case 403:
          return {
            code: 'FORBIDDEN',
            message: '权限不足，无法访问此功能',
            details: data,
            status
          };

        case 404:
          return {
            code: 'NOT_FOUND',
            message: '请求的资源不存在或服务不可用',
            details: data,
            status
          };

        case 429:
          return {
            code: 'RATE_LIMIT',
            message: '请求过于频繁，请稍后重试',
            details: data,
            status
          };

        case 500:
          return {
            code: 'INTERNAL_ERROR',
            message: '服务器内部错误，请稍后重试',
            details: data,
            status
          };

        case 502:
        case 503:
        case 504:
          return {
            code: 'SERVICE_UNAVAILABLE',
            message: '服务暂时不可用，请稍后重试',
            details: data,
            status
          };

        default:
          return {
            code: 'UNKNOWN_ERROR',
            message: `请求失败: ${message}`,
            details: data,
            status
          };
      }
    }

    // 网络错误
    if (error.code === 'ECONNREFUSED' || error.code === 'ERR_NETWORK') {
      return {
        code: 'NETWORK_ERROR',
        message: '网络连接失败，请检查服务是否启动',
        details: error
      };
    }

    // 超时错误
    if (error.code === 'ECONNABORTED' || error.message.includes('timeout')) {
      return {
        code: 'TIMEOUT',
        message: '请求超时，请稍后重试',
        details: error
      };
    }

    // 业务逻辑错误
    if (error instanceof Error) {
      return {
        code: 'BUSINESS_ERROR',
        message: error.message,
        details: error.stack
      };
    }

    // 未知错误
    return {
      code: 'UNKNOWN_ERROR',
      message: '未知错误，请稍后重试',
      details: error
    };
  }

  /**
   * 获取友好的400错误信息
   */
  private static getBadRequestMessage(originalMessage: string): string {
    if (originalMessage.includes('description')) {
      return '策略描述不能为空';
    }
    if (originalMessage.includes('parameters')) {
      return '策略参数错误，请检查输入参数';
    }
    if (originalMessage.includes('code')) {
      return '策略代码有误，请检查语法错误';
    }
    if (originalMessage.includes('validation')) {
      return '数据验证失败，请检查输入格式';
    }
    return `请求参数错误: ${originalMessage}`;
  }

  /**
   * 根据错误类型获取用户操作建议
   */
  static getSuggestion(error: APIError): string {
    switch (error.code) {
      case 'BAD_REQUEST':
        return '请检查输入参数是否正确';
      case 'UNAUTHORIZED':
        return '请检查API密钥配置或重新登录';
      case 'FORBIDDEN':
        return '请联系管理员获取相应权限';
      case 'NOT_FOUND':
        return '请确认资源是否存在或URL是否正确';
      case 'RATE_LIMIT':
        return '请稍等片刻后重试';
      case 'NETWORK_ERROR':
        return '请检查网络连接和服务状态';
      case 'TIMEOUT':
        return '请稍后重试或检查网络稳定性';
      case 'SERVICE_UNAVAILABLE':
        return '服务正在维护中，请稍后重试';
      case 'INTERNAL_ERROR':
        return '请联系技术支持或稍后重试';
      default:
        return '请重试或联系技术支持';
    }
  }

  /**
   * 重试机制配置
   */
  static getRetryConfig(error: APIError): { shouldRetry: boolean; delay: number; maxRetries: number } {
    switch (error.code) {
      case 'NETWORK_ERROR':
      case 'TIMEOUT':
      case 'SERVICE_UNAVAILABLE':
        return { shouldRetry: true, delay: 2000, maxRetries: 3 };
      case 'RATE_LIMIT':
        return { shouldRetry: true, delay: 5000, maxRetries: 2 };
      case 'INTERNAL_ERROR':
        return { shouldRetry: true, delay: 1000, maxRetries: 1 };
      default:
        return { shouldRetry: false, delay: 0, maxRetries: 0 };
    }
  }
}

/**
 * 带重试机制的API调用包装器
 */
export async function withRetry<T>(
  apiCall: () => Promise<T>,
  context: string = 'API调用'
): Promise<T> {
  let lastError: any;

  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      return await apiCall();
    } catch (error) {
      lastError = error;
      const apiError = APIErrorHandler.handleError(error, context);
      const retryConfig = APIErrorHandler.getRetryConfig(apiError);

      if (!retryConfig.shouldRetry || attempt >= retryConfig.maxRetries) {
        throw apiError;
      }

      console.warn(`${context}失败，第${attempt}次重试 (${retryConfig.delay}ms后):`, apiError.message);
      await new Promise(resolve => setTimeout(resolve, retryConfig.delay));
    }
  }

  throw APIErrorHandler.handleError(lastError, context);
}

/**
 * 错误边界组件错误处理
 */
export function handleComponentError(error: Error, errorInfo: any): APIError {
  console.error('组件错误:', error, errorInfo);

  return {
    code: 'COMPONENT_ERROR',
    message: '组件出现错误，请刷新页面重试',
    details: {
      error: error.message,
      stack: error.stack,
      componentStack: errorInfo.componentStack
    }
  };
}

export default APIErrorHandler;
