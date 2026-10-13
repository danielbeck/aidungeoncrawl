import { useState } from 'react'
import { Link } from 'react-router-dom'
import './AuthPage.scss'

export default function ProfilePage({ user, onLogout }) {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (event) => {
    event.preventDefault()
    if (submitting) return
    setSubmitting(true)
    setError('')
    try {
      await onLogout()
    } catch (err) {
      setError(err.message)
      setSubmitting(false)
    }
  }

  return <section className="auth-page" aria-labelledby="profile-title">
    <Link className="auth-back" to="/">Back to your todo list</Link>
    <header className="auth-heading">
      <p>Your account</p>
      <h1 id="profile-title">Your profile.</h1>
    </header>
    <div className="auth-card">
      <div className="auth-panel">
        <h2>{user}</h2>
        <p className="auth-description">You are logged in to your account.</p>
        <form onSubmit={handleSubmit} aria-busy={submitting}>
          {error && <p className="auth-error" role="alert">{error}</p>}
          <button className="auth-submit" type="submit" disabled={submitting}>
            {submitting ? 'Logging out...' : 'Log out'}
          </button>
        </form>
      </div>
    </div>
  </section>
}
