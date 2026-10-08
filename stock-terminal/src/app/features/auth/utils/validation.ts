/**
 * 表单验证工具
 */

import type { FormInstance } from 'antd';

// 邮箱验证正则
export const EMAIL_REGEX = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;

// 手机号验证正则（中国大陆）
export const PHONE_REGEX = /^1[3-9]\d{9}$/;

// 用户名验证正则
export const USERNAME_REGEX = /^[a-zA-Z0-9_]{3,20}$/;

// 密码强度检查
export interface PasswordStrengthResult {
  score: number;
  level: 'weak' | 'medium' | 'strong' | 'very-strong';
  feedback: string[];
  passed: boolean;
}

/**
 * 验证邮箱格式
 */
export const validateEmail = (email: string): { valid: boolean; message?: string } => {
  if (!email) {
    return { valid: false, message: '请输入邮箱地址' };
  }

  if (!EMAIL_REGEX.test(email)) {
    return { valid: false, message: '请输入有效的邮箱地址' };
  }

  return { valid: true };
};

/**
 * 验证手机号格式
 */
export const validatePhone = (phone: string): { valid: boolean; message?: string } => {
  if (!phone) {
    return { valid: true }; // 手机号为可选
  }

  if (!PHONE_REGEX.test(phone)) {
    return { valid: false, message: '请输入有效的中国大陆手机号' };
  }

  return { valid: true };
};

/**
 * 验证用户名格式
 */
export const validateUsername = (username: string): { valid: boolean; message?: string } => {
  if (!username) {
    return { valid: false, message: '请输入用户名' };
  }

  if (username.length < 3) {
    return { valid: false, message: '用户名至少3个字符' };
  }

  if (username.length > 20) {
    return { valid: false, message: '用户名最多20个字符' };
  }

  if (!USERNAME_REGEX.test(username)) {
    return { valid: false, message: '用户名只能包含字母、数字和下划线' };
  }

  // 检查是否为纯数字
  if (/^\d+$/.test(username)) {
    return { valid: false, message: '用户名不能为纯数字' };
  }

  // 检查是否以下划线开头或结尾
  if (username.startsWith('_') || username.endsWith('_')) {
    return { valid: false, message: '用户名不能以下划线开头或结尾' };
  }

  return { valid: true };
};

/**
 * 验证密码强度
 */
export const validatePasswordStrength = (password: string): PasswordStrengthResult => {
  let score = 0;
  const feedback: string[] = [];

  // 长度检查
  if (password.length >= 8) {
    score += 1;
  } else {
    feedback.push('密码长度至少8个字符');
  }

  if (password.length >= 12) {
    score += 1;
  }

  // 包含小写字母
  if (/[a-z]/.test(password)) {
    score += 1;
  } else {
    feedback.push('密码应包含小写字母');
  }

  // 包含大写字母
  if (/[A-Z]/.test(password)) {
    score += 1;
  } else {
    feedback.push('密码应包含大写字母');
  }

  // 包含数字
  if (/\d/.test(password)) {
    score += 1;
  } else {
    feedback.push('密码应包含数字');
  }

  // 包含特殊字符
  if (/[!@#$%^&*(),.?":{}|<>]/.test(password)) {
    score += 1;
  } else {
    feedback.push('密码应包含特殊字符');
  }

  // 不包含常见弱密码
  const commonPasswords = [
    'password', '123456', '123456789', 'qwerty', 'abc123',
    '111111', 'password123', 'admin', 'letmein', 'welcome'
  ];

  if (commonPasswords.includes(password.toLowerCase())) {
    score = Math.max(0, score - 2);
    feedback.push('请使用更安全的密码，避免使用常见密码');
  }

  // 不包含用户名相关字符
  const checkUsernameRelated = (password: string, username: string): boolean => {
    if (!username) return true;
    const lowerPassword = password.toLowerCase();
    const lowerUsername = username.toLowerCase();

    // 检查密码是否包含用户名
    if (lowerPassword.includes(lowerUsername)) {
      return false;
    }

    // 检查用户名是否包含密码
    if (lowerUsername.includes(lowerPassword)) {
      return false;
    }

    return true;
  };

  // 计算密码强度等级
  let level: PasswordStrengthResult['level'];
  let passed: boolean;

  if (score <= 2) {
    level = 'weak';
    passed = false;
  } else if (score <= 3) {
    level = 'medium';
    passed = false;
  } else if (score <= 4) {
    level = 'strong';
    passed = true;
  } else {
    level = 'very-strong';
    passed = true;
  }

  return {
    score,
    level,
    feedback,
    passed
  };
};

/**
 * 验证密码确认
 */
export const validatePasswordConfirm = (
  password: string,
  confirmPassword: string
): { valid: boolean; message?: string } => {
  if (!confirmPassword) {
    return { valid: false, message: '请确认密码' };
  }

  if (password !== confirmPassword) {
    return { valid: false, message: '两次输入的密码不一致' };
  }

  return { valid: true };
};

/**
 * 实时验证表单字段
 */
export const validateField = (
  field: string,
  value: string,
  context?: { username?: string; password?: string }
): { valid: boolean; message?: string } => {
  switch (field) {
    case 'email':
      return validateEmail(value);

    case 'phone':
      return validatePhone(value);

    case 'username':
      return validateUsername(value);

    case 'password':
      const passwordResult = validatePasswordStrength(value);
      return {
        valid: passwordResult.passed,
        message: passwordResult.passed ? undefined : passwordResult.feedback[0]
      };

    case 'confirmPassword':
      return validatePasswordConfirm(context?.password || '', value);

    default:
      return { valid: true };
  }
};

/**
 * 为Ant Design Form创建验证规则
 */
export const createValidationRules = (field: string, context?: { username?: string; password?: string }) => {
  const rules: any[] = [];

  // 基础必填验证
  if (['email', 'username', 'password'].includes(field)) {
    rules.push({ required: true, message: `请输入${getFieldName(field)}` });
  }

  // 字段特定验证
  switch (field) {
    case 'email':
      rules.push({
        type: 'email',
        message: '请输入有效的邮箱地址'
      });
      break;

    case 'username':
      rules.push({
        min: 3,
        max: 20,
        message: '用户名长度为3-20个字符'
      });
      rules.push({
        pattern: USERNAME_REGEX,
        message: '用户名只能包含字母、数字和下划线'
      });
      break;

    case 'password':
      rules.push({
        min: 8,
        message: '密码至少8个字符'
      });
      rules.push({
        validator: (_: any, value: string) => {
          const result = validatePasswordStrength(value);
          return result.passed ? Promise.resolve() : Promise.reject(result.feedback[0]);
        }
      });
      break;

    case 'confirmPassword':
      rules.push({
        validator: (_: any, value: string) => {
          const result = validatePasswordConfirm(context?.password || '', value);
          return result.valid ? Promise.resolve() : Promise.reject(result.message);
        }
      });
      break;

    case 'phone':
      rules.push({
        pattern: PHONE_REGEX,
        message: '请输入有效的中国大陆手机号'
      });
      break;
  }

  return rules;
};

/**
 * 获取字段中文名称
 */
export const getFieldName = (field: string): string => {
  const fieldNames: Record<string, string> = {
    email: '邮箱地址',
    username: '用户名',
    password: '密码',
    confirmPassword: '确认密码',
    phone: '手机号码',
    full_name: '真实姓名'
  };

  return fieldNames[field] || field;
};

/**
 * 防抖函数
 */
export const debounce = <T extends (...args: any[]) => any>(
  func: T,
  wait: number
): ((...args: Parameters<T>) => void) => {
  let timeout: NodeJS.Timeout;

  return (...args: Parameters<T>) => {
    clearTimeout(timeout);
    timeout = setTimeout(() => func(...args), wait);
  };
};

/**
 * 实时验证Hook辅助函数
 */
export const createFieldValidator = (
  form: FormInstance,
  field: string,
  context?: { username?: string; password?: string }
) => {
  return debounce((value: string) => {
    const result = validateField(field, value, context);

    if (!result.valid) {
      form.setFields([
        {
          name: field,
          errors: [result.message]
        }
      ]);
    } else {
      form.setFields([
        {
          name: field,
          errors: []
        }
      ]);
    }
  }, 300);
};

export default {
  validateEmail,
  validatePhone,
  validateUsername,
  validatePasswordStrength,
  validatePasswordConfirm,
  validateField,
  createValidationRules,
  getFieldName,
  debounce,
  createFieldValidator,
};
