import React from 'react'
import './Rail.css'

export default function Rail() {
  return (
    <aside className="rail">
      <div className="rail__brand">
        <div className="rail__brand-icon">VB</div>
        <span>Vienna<br/>Biocenter</span>
      </div>

      <nav className="rail__nav">
        <a className="rail__nav-item rail__nav-item--active" title="Home">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/>
          </svg>
          <span>Home</span>
        </a>
        <a className="rail__nav-item" title="Projects">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M3 3h8v8H3zm0 10h8v8H3zm10-10h8v8h-8zm0 10h8v8h-8z"/>
          </svg>
          <span>Projects</span>
        </a>
        <a className="rail__nav-item" title="Queues">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M4 6h16v2H4zm0 5h16v2H4zm0 5h16v2H4z"/>
          </svg>
          <span>Queues</span>
        </a>
        <a className="rail__nav-item" title="Alerts">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 22c1.1 0 2-.9 2-2h-4a2 2 0 002 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/>
          </svg>
          <span>Alerts</span>
        </a>
      </nav>

      <div className="rail__user">
        <div className="rail__avatar">PP</div>
        <span>Peter</span>
      </div>
    </aside>
  )
}
