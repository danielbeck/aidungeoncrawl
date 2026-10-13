import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { fetchAPI } from '../api';
import './AuthPage.scss';

export default function PasswordRecoveryPage({ reset = false, onReset }) {
    const location = useLocation();
    const navigate = useNavigate();
    const [token, setToken] = useState(() => new URLSearchParams(location.hash.slice(1)).get('token') || '');
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [confirmation, setConfirmation] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState(null);
    const [message, setMessage] = useState('');

    useEffect(() => {
        if (reset && location.hash) {
            setToken(new URLSearchParams(location.hash.slice(1)).get('token') || '');
            setMessage('');
            setError(null);
            setPassword('');
            setConfirmation('');
            navigate(location.pathname, { replace: true });
        }
    }, [reset, location.hash, location.pathname, navigate]);

    const handleSubmit = async (event) => {
        event.preventDefault();
        if (submitting) return;
        setError(null);
        if (reset && password !== confirmation) {
            setError('Passwords do not match');
            return;
        }
        setSubmitting(true);
        try {
            const data = await fetchAPI(reset ? '/auth/reset-password' : '/auth/forgot-password', {
                method: 'POST',
                credentials: 'include',
                body: JSON.stringify(reset
                    ? { token, password, confirm_password: confirmation }
                    : { email }),
            });
            setPassword('');
            setConfirmation('');
            setMessage(data.message);
            if (reset) onReset?.();
        } catch (err) {
            setError(err.message);
        } finally {
            setSubmitting(false);
        }
    };

    return <section className="auth-page" aria-labelledby="recovery-title">
        <Link className="auth-back" to="/auth">Back to login</Link>
        <header className="auth-heading">
            <p>Your account</p>
            <h1 id="recovery-title">{reset ? 'A fresh start.' : 'Forgot your password?'}</h1>
        </header>
        <div className="auth-card">
            <div className="auth-panel">
                <h2>{reset ? 'Choose a new password.' : 'Get a reset link.'}</h2>
                <p className="auth-description">{reset
                    ? 'Enter and confirm your new password. Reset links expire after 15 minutes.'
                    : 'Enter your account email and we will email you a link valid for 15 minutes.'}</p>
                {message ? <>
                    <p className="auth-success" role="status">{message}</p>
                    <Link className="auth-back" to="/auth">Log in</Link>
                </> : reset && !token ? <>
                    <p className="auth-error" role="alert">This reset link is missing its token. Please request a new one.</p>
                    <Link className="auth-back" to="/forgot-password">Request a new reset link</Link>
                </> : <form onSubmit={handleSubmit} aria-busy={submitting}>
                    {reset ? <>
                        <div className="auth-field">
                            <label htmlFor="reset-password">New password</label>
                            <input id="reset-password" name="password" type="password" autoComplete="new-password"
                                value={password} onChange={event => setPassword(event.target.value)}
                                minLength={8} maxLength={128} disabled={submitting} required />
                            <p className="auth-description">Use 8-128 characters, including an uppercase letter, a lowercase letter, and a number.</p>
                        </div>
                        <div className="auth-field">
                            <label htmlFor="reset-confirmation">Confirm new password</label>
                            <input id="reset-confirmation" name="confirm_password" type="password" autoComplete="new-password"
                                value={confirmation} onChange={event => setConfirmation(event.target.value)}
                                minLength={8} maxLength={128} disabled={submitting} required />
                        </div>
                    </> : <div className="auth-field">
                        <label htmlFor="recovery-email">Email</label>
                        <input id="recovery-email" name="email" type="email" autoComplete="email"
                            autoCapitalize="none" spellCheck={false} maxLength={254}
                            value={email} onChange={event => setEmail(event.target.value)} disabled={submitting} required />
                    </div>}
                    {error && <p className="auth-error" role="alert">{error}</p>}
                    <button className="auth-submit" type="submit" disabled={submitting}>
                        {submitting ? 'Please wait...' : reset ? 'Update password' : 'Email reset link'}
                    </button>
                    {reset && <Link className="auth-back" to="/forgot-password">Request a new reset link</Link>}
                </form>}
            </div>
        </div>
    </section>;
}
