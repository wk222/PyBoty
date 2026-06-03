import { createApp } from 'vue';
import IdeWorkspaceShell from '/static/components/IdeWorkspaceShell.js?v=20260531-3';
import ToastContainer from '/static/components/Toast.js?v=20260531-3';

const App = {
  components: { IdeWorkspaceShell, ToastContainer },
  template: `
    <div class="ide-shell">
      <IdeWorkspaceShell />
      <ToastContainer />
    </div>
  `,
};

createApp(App).mount('#app');
