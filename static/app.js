import { createApp } from 'vue';
import { createRouter, createWebHashHistory } from 'vue-router';

import TopBar from '/static/components/TopBar.js';
import SideNav from '/static/components/SideNav.js';
import ToastContainer from '/static/components/Toast.js';
import CommandPalette from '/static/components/CommandPalette.js';
import StatusBar from '/static/components/StatusBar.js';
import OnboardingGuide from '/static/components/OnboardingGuide.js';
import CliPanel from '/static/components/CliPanel.js';

import Dashboard from '/static/views/Dashboard.js';
import EcosystemView from '/static/views/EcosystemView.js';
import SystemModel from '/static/views/SystemModel.js';
import ChatView from '/static/views/ChatView.js';
import AgentList from '/static/views/AgentList.js';
import ToolList from '/static/views/ToolList.js';
import SkillList from '/static/views/SkillList.js';
import WorkflowList from '/static/views/WorkflowList.js';
import WorkflowBuilder from '/static/views/WorkflowBuilder.js';
import AppList from '/static/views/AppList.js';
import ScheduleList from '/static/views/ScheduleList.js';
import TaskPanel from '/static/views/TaskPanel.js';
import HubView from '/static/views/HubView.js';
import GovernanceDashboard from '/static/views/GovernanceDashboard.js';
import MemoryView from '/static/views/MemoryView.js';
import TracingView from '/static/views/TracingView.js';
import AppMatrixView from '/static/views/AppMatrixView.js';
import DebugPanel from '/static/views/DebugPanel.js';
import Settings from '/static/views/Settings.js';
import IntegrationsView from '/static/views/IntegrationsView.js';
import TeamConsoleView from '/static/views/TeamConsoleView.js';
import CliView from '/static/views/CliView.js';
const routes = [
  { path: '/', redirect: '/chat' },
  { path: '/dashboard', component: Dashboard },
  { path: '/ecosystem', component: EcosystemView },
  { path: '/system', component: SystemModel },
  { path: '/chat', component: ChatView },
  { path: '/agents', component: AgentList },
  { path: '/tools', component: ToolList },
  { path: '/skills', component: SkillList },
  { path: '/workflows', component: WorkflowList },
  { path: '/workflows/builder', component: WorkflowBuilder },
  { path: '/workflows/builder/:name', component: WorkflowBuilder },
  { path: '/apps', component: AppList },
  { path: '/hub', component: HubView },
  { path: '/schedules', component: ScheduleList },
  { path: '/tasks', component: TaskPanel },
  { path: '/governance', component: GovernanceDashboard, alias: ['/approvals', '/governance/policy'] },
  { path: '/memory', component: MemoryView },
  { path: '/tracing', component: TracingView },
  { path: '/app-matrix', component: AppMatrixView },
  { path: '/debug', component: DebugPanel },
  { path: '/settings', component: Settings },
  { path: '/integrations', component: IntegrationsView },
  { path: '/team', component: TeamConsoleView },
  { path: '/cli', component: CliView },
];

const router = createRouter({
  history: createWebHashHistory(),
  routes,
});

const App = {
  components: { TopBar, SideNav, ToastContainer, CommandPalette, StatusBar, OnboardingGuide, CliPanel },
  template: `
    <div class="mx-layout">
      <TopBar />
      <div class="mx-body">
        <SideNav />
        <main class="mx-main">
          <router-view v-slot="{ Component }">
            <transition name="mx-fade" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </main>
      </div>
      <CliPanel />
      <StatusBar />
      <ToastContainer />
      <CommandPalette />
      <OnboardingGuide />
    </div>
  `,
};

const app = createApp(App);
app.use(router);
app.mount('#app');
