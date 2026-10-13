import { useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { fetchAPI } from '../api';
import './AuthPage.scss';

const modes = [
    { id: 'login', label: 'Log in', description: 'Welcome back. Log in to your account.' },
    { id: 'register', label: 'Register', description: 'Create an account to get started.' },
];

export default function AuthPage({ onAuth }) {
    const [mode, setMode] = useState('login');
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const tabRefs = useRef([]);
    const navigate = useNavigate();

    const changeMode = (nextMode) => {
        setMode(nextMode);
        setError(null);
        setPassword('');
    };

    const handleTabKeyDown = (event, index) => {
        let nextIndex;
        if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') nextIndex = 1 - index;
        else if (event.key === 'Home') nextIndex = 0;
        else if (event.key === 'End') nextIndex = 1;
        else return;
        event.preventDefault();
        changeMode(modes[nextIndex].id);
        tabRefs.current[nextIndex]?.focus();
    };

    const handleSubmit = async (event) => {
        event.preventDefault();
        if (submitting) return;
        setError(null);
        setSubmitting(true);
        const endpoint = mode === 'register' ? '/auth/register' : '/auth/login';
        try {
            const data = await fetchAPI(endpoint, {
                method: 'POST',
                credentials: 'include',
                body: JSON.stringify({ email, password }),
            });
            onAuth && onAuth(data);
            navigate('/');
        } catch (err) {
            setError(err.message);
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <section className="auth-page" aria-labelledby="auth-title">
            <Link className="auth-back" to="/">Back to your todo list</Link>
            <header className="auth-heading">
                <p>Your account</p>
                <h1 id="auth-title">Come on in.</h1>
            </header>
            <div className="auth-card">
                <div className="auth-tabs" role="tablist" aria-label="Account access">
                    {modes.map(({ id, label }, index) => <button
                        key={id}
                        ref={element => { tabRefs.current[index] = element; }}
                        type="button"
                        role="tab"
                        id={`auth-tab-${id}`}
                        aria-controls={`auth-panel-${id}`}
                        aria-selected={mode === id}
                        tabIndex={mode === id ? 0 : -1}
                        disabled={submitting}
                        onClick={() => changeMode(id)}
                        onKeyDown={event => handleTabKeyDown(event, index)}
                    >{label}</button>)}
                </div>
                {modes.map(({ id, label, description }) => <div
                    key={id}
                    className="auth-panel"
                    role="tabpanel"
                    id={`auth-panel-${id}`}
                    aria-labelledby={`auth-tab-${id}`}
                    hidden={mode !== id}
                >
                    <h2>{id === 'login' ? 'Welcome back.' : 'Make yourself at home.'}</h2>
                    <p className="auth-description">{description}</p>
                    <form onSubmit={handleSubmit} aria-busy={submitting}>
                        <div className="auth-field">
                            <label htmlFor={`auth-email-${id}`}>Email</label>
                            <input
                                id={`auth-email-${id}`}
                                name="email"
                                type="email"
                                autoComplete="username"
                                autoCapitalize="none"
                                spellCheck={false}
                                value={email}
                                onChange={event => setEmail(event.target.value)}
                                maxLength={254}
                                disabled={submitting}
                                required
                            />
                        </div>
                        <div className="auth-field">
                            <label htmlFor={`auth-password-${id}`}>Password</label>
                            <input
                                id={`auth-password-${id}`}
                                name="password"
                                type="password"
                                autoComplete={id === 'register' ? 'new-password' : 'current-password'}
                                value={password}
                                onChange={event => setPassword(event.target.value)}
                                disabled={submitting}
                                aria-describedby={error ? `auth-error-${id}` : undefined}
                                minLength={id === 'register' ? 8 : undefined}
                                maxLength={128}
                                required
                            />
                            {id === 'register' && <p className="auth-description">Use 8-128 characters, including an uppercase letter, a lowercase letter, and a number.</p>}
                        </div>
                        {id === 'login' && <Link className="auth-back" to="/forgot-password">Forgot my password</Link>}
                        {error && mode === id && <p className="auth-error" id={`auth-error-${id}`} role="alert">{error}</p>}
                        <button className="auth-submit" type="submit" disabled={submitting}>
                            {submitting ? id === 'register' ? 'Creating account...' : 'Logging in...' : label}
                        </button>
                    </form>
                </div>)}
            </div>
        </section>
    );
}
