export default {
  name: 'SideNav',
  template: `
    <nav class="mx-sidenav">
      <router-link to="/" class="mx-nav-item" exact-active-class="active" title="Dashboard">
        <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>
          <rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>
        </svg>
        <span class="mx-nav-label">Dashboard</span>
      </router-link>
      <router-link to="/chat" class="mx-nav-item" active-class="active" title="Chat">
        <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
        <span class="mx-nav-label">Chat</span>
      </router-link>
      <router-link to="/agents" class="mx-nav-item" active-class="active" title="Agents">
        <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="11" width="18" height="11" rx="2"/><circle cx="12" cy="5" r="4"/>
          <line x1="8" y1="16" x2="8" y2="16.01"/><line x1="16" y1="16" x2="16" y2="16.01"/>
        </svg>
        <span class="mx-nav-label">Agents</span>
      </router-link>
      <router-link to="/tools" class="mx-nav-item" active-class="active" title="Tools">
        <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
        </svg>
        <span class="mx-nav-label">Tools</span>
      </router-link>
      <router-link to="/skills" class="mx-nav-item" active-class="active" title="Skills">
        <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
        </svg>
        <span class="mx-nav-label">Skills</span>
      </router-link>
      <router-link to="/workflows" class="mx-nav-item" active-class="active" title="Workflows">
        <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="5" r="3"/><line x1="12" y1="8" x2="12" y2="12"/>
          <circle cx="6" cy="19" r="3"/><circle cx="18" cy="19" r="3"/>
          <line x1="12" y1="12" x2="6" y2="16"/><line x1="12" y1="12" x2="18" y2="16"/>
        </svg>
        <span class="mx-nav-label">Workflows</span>
      </router-link>
      <router-link to="/apps" class="mx-nav-item" active-class="active" title="Apps">
        <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <rect x="2" y="2" width="9" height="9" rx="2"/><rect x="13" y="2" width="9" height="9" rx="2"/>
          <rect x="2" y="13" width="9" height="9" rx="2"/><rect x="13" y="13" width="9" height="9" rx="2"/>
        </svg>
        <span class="mx-nav-label">Apps</span>
      </router-link>
      <router-link to="/hub" class="mx-nav-item" active-class="active" title="Hub">
        <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
          <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
          <line x1="12" y1="22.08" x2="12" y2="12"/>
        </svg>
        <span class="mx-nav-label">Hub</span>
      </router-link>
      <router-link to="/schedules" class="mx-nav-item" active-class="active" title="Schedules">
        <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
        </svg>
        <span class="mx-nav-label">Schedules</span>
      </router-link>
      <router-link to="/approvals" class="mx-nav-item" active-class="active" title="Approvals">
        <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>
        </svg>
        <span class="mx-nav-label">Approvals</span>
      </router-link>
      <router-link to="/governance" class="mx-nav-item" active-class="active" title="Governance">
        <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
        </svg>
        <span class="mx-nav-label">Governance</span>
      </router-link>
      <router-link to="/debug" class="mx-nav-item" active-class="active" title="Debug Panel">
        <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 20h.01"/><path d="M8.56 15.69a5 5 0 0 1 6.88 0"/><path d="M5.12 12.25a9 9 0 0 1 13.76 0"/><path d="M1.67 8.81a13 13 0 0 1 20.66 0"/>
        </svg>
        <span class="mx-nav-label">Debug</span>
      </router-link>
      <div class="mx-nav-spacer"></div>
      <router-link to="/settings" class="mx-nav-item" active-class="active" title="Settings">
        <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
        </svg>
        <span class="mx-nav-label">Settings</span>
      </router-link>
    </nav>
  `
};
