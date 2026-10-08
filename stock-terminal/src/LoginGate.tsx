import { useState } from 'react';
import { Alert, Button, Card, Form, Input, Typography } from 'antd';
import { Lock, Monitor, User } from 'lucide-react';
import { authService } from './app/features/auth/services/authService';

/**
 * 老版个股终端独立版登录闸门。
 * 后端在本机 8000（quantmind 容器），页面上登录信息与主应用互不影响。
 * 登录成功后 authService 会把 access_token/user 写入当前 origin 的 localStorage，
 * 刷新页面自动重进终端。
 */
export default function LoginGate({ children }: { children: React.ReactElement }) {
  const [authed, setAuthed] = useState(() => Boolean(localStorage.getItem('access_token')));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (authed) return children;

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    setError(null);
    try {
      await authService.login({
        email_or_username: values.username,
        password: values.password,
      });
      setAuthed(true);
    } catch (e: any) {
      setError(
        String(e?.message || e || '登录失败：请确认后端（localhost:8000）已启动、账号密码正确'),
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        height: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(160deg, #0d1a26 0%, #14202e 55%, #1d2d3f 100%)',
      }}
    >
      <Card style={{ width: 400, borderRadius: 12 }}>
        <div style={{ textAlign: 'center', marginBottom: 20 }}>
          <Monitor size={36} color="#1677ff" />
          <Typography.Title level={4} style={{ marginTop: 8, marginBottom: 4 }}>
            老版个股终端 · 本地独立版
          </Typography.Title>
          <Typography.Text type="secondary">
            数据经 localhost:8000 本机后端；与主应用（8080）登录态互不影响
          </Typography.Text>
        </div>
        {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
        <Form layout="vertical" onFinish={onFinish} initialValues={{ username: 'admin' }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<User size={14} />} placeholder="admin" autoFocus />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<Lock size={14} />} placeholder="密码" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading}>
            登录并进入终端
          </Button>
        </Form>
      </Card>
    </div>
  );
}