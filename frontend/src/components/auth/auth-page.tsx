import { createContext, useContext, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { ArrowRight, Compass, Eye, EyeOff, LogIn, UserPlus } from 'lucide-react';
import { ApiError } from '@/lib/api';
import { clearAuth, getStoredUser, login, register, type AuthUser } from '@/lib/auth';

const dataMode: 'demo' | 'api' = import.meta.env.VITE_DATA_MODE === 'api' ? 'api' : 'demo';

interface AuthContextValue {
  user: AuthUser | null;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => getStoredUser());
  const signIn = async (email: string, password: string) => setUser(await login(email, password, dataMode));
  const signUp = async (email: string, password: string) => setUser(await register(email, password, dataMode));
  const signOut = () => { clearAuth(); setUser(null); };
  return <AuthContext.Provider value={{ user, signIn, signUp, signOut }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used within AuthProvider');
  return value;
}

export function AuthGate({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const location = useLocation();
  if (!user && location.pathname !== '/auth') return <Navigate to="/auth" replace state={{ from: location.pathname }} />;
  if (user && location.pathname === '/auth') return <Navigate to="/" replace />;
  return <>{children}</>;
}

export function AuthPage() {
  const { user, signIn, signUp } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [registerMode, setRegisterMode] = useState(() => new URLSearchParams(location.search).get('mode') === 'register');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  if (user) return <Navigate to="/" replace />;

  function switchMode(next: boolean) {
    setRegisterMode(next); setError(''); setPassword(''); setConfirm('');
    navigate(next ? '/auth?mode=register' : '/auth', { replace: true });
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError('');
    if (registerMode && password !== confirm) { setError('两次输入的密码不一致。'); return; }
    setBusy(true);
    try {
      if (registerMode) await signUp(email, password); else await signIn(email, password);
      navigate('/');
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : '操作失败，请稍后重试。');
    } finally { setBusy(false); }
  }

  return <main className="auth-page">
    <section className="auth-intro" aria-label="Trip Agents">
      <Link to="/auth" className="auth-brand"><span><Compass size={22} /></span><strong>Trip Agents</strong></Link>
      <div className="auth-intro-copy"><p className="auth-kicker">旅行工作台</p><h1>把灵感，变成<br /><em>可执行的行程。</em></h1><p>登录后保存你的旅行计划，随时继续完善每一个目的地。</p></div>
      <p className="auth-intro-footer">Plan less. Experience more.</p>
    </section>
    <section className="auth-panel">
      <div className="auth-panel-inner">
        <div className="auth-mobile-brand"><Compass size={18} /><strong>Trip Agents</strong></div>
        <div className="auth-heading"><span className="auth-heading-icon">{registerMode ? <UserPlus size={18} /> : <LogIn size={18} />}</span><div><h2>{registerMode ? '创建你的账号' : '欢迎回来'}</h2><p>{registerMode ? '用邮箱注册，保存你的专属旅行工作台' : '登录后继续规划你的下一段旅程'}</p></div></div>
        <div className="auth-tabs" role="tablist"><button type="button" role="tab" aria-selected={!registerMode} onClick={() => switchMode(false)}>登录</button><button type="button" role="tab" aria-selected={registerMode} onClick={() => switchMode(true)}>注册</button></div>
        <form className="auth-form" onSubmit={submit} noValidate>
          <label>邮箱地址<input type="email" value={email} onChange={event => setEmail(event.target.value)} placeholder="name@example.com" autoComplete="email" required /></label>
          <label>密码<div className="password-field"><input type={showPassword ? 'text' : 'password'} value={password} onChange={event => setPassword(event.target.value)} placeholder="至少 8 位字符" autoComplete={registerMode ? 'new-password' : 'current-password'} minLength={8} required /><button type="button" aria-label={showPassword ? '隐藏密码' : '显示密码'} title={showPassword ? '隐藏密码' : '显示密码'} onClick={() => setShowPassword(value => !value)}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></label>
          {registerMode && <label>确认密码<input type="password" value={confirm} onChange={event => setConfirm(event.target.value)} placeholder="再次输入密码" autoComplete="new-password" minLength={8} required /></label>}
          {error && <p className="auth-error" role="alert">{error}</p>}
          <button className="auth-submit" type="submit" disabled={busy}>{busy ? '请稍候…' : registerMode ? '创建账号' : '登录'}<ArrowRight size={16} /></button>
        </form>
        <p className="auth-switch">{registerMode ? '已经有账号？' : '还没有账号？'}<button type="button" onClick={() => switchMode(!registerMode)}>{registerMode ? '返回登录' : '邮箱注册'}</button></p>
        {dataMode === 'demo' && <p className="auth-demo-note">当前为本地演示模式，账号仅保存在此浏览器。</p>}
      </div>
    </section>
  </main>;
}
