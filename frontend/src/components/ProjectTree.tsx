import React from 'react'
import './ProjectTree.css'

export default function ProjectTree() {
  return (
    <aside className="tree">
      <div className="tree__head">
        Macrophage Stimulation Pilot
        <svg className="chev" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
          <path d="M5 8l5 5 5-5z"/>
        </svg>
      </div>

      <section className="tree__section">
        <div className="tree__section-head open">
          <svg className="icon" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
            <path d="M3 5a2 2 0 012-2h10a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V5z"/>
          </svg>
          Data
          <svg className="chev ml-auto" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
            <path d="M5 8l5 5 5-5z"/>
          </svg>
        </div>
        <div className="tree__items">
          <div className="tree__item is-active">
            <svg className="icon" width="14" height="14" viewBox="0 0 20 20" fill="var(--accent)">
              <path d="M4 4a2 2 0 00-2 2v8a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2H4z"/>
            </svg>
            <span className="tree__item-label">
              macrophage-pilot
              <span className="meta"><span className="dot" />data folder</span>
            </span>
          </div>
        </div>
      </section>

      <section className="tree__section">
        <div className="tree__section-head">
          <svg className="icon" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
            <path d="M4 5h12M4 10h12M4 15h7"/>
          </svg>
          Figures
          <svg className="chev ml-auto" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
            <path d="M8 5l5 5-5 5z"/>
          </svg>
        </div>
      </section>

      <section className="tree__section">
        <div className="tree__section-head">
          <svg className="icon" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
            <circle cx="10" cy="10" r="7"/>
          </svg>
          Findings
          <svg className="chev ml-auto" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
            <path d="M8 5l5 5-5 5z"/>
          </svg>
        </div>
      </section>

      <section className="tree__section">
        <div className="tree__section-head">
          <svg className="icon" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
            <path d="M6 2h8l4 4v12a2 2 0 01-2 2H4a2 2 0 01-2-2V4a2 2 0 012-2z"/>
          </svg>
          Manuscript
          <svg className="chev ml-auto" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
            <path d="M8 5l5 5-5 5z"/>
          </svg>
        </div>
      </section>
    </aside>
  )
}
