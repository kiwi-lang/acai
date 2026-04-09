import { HashRouter as Router, Routes, Route } from 'react-router-dom';
import {
  ChakraProvider,
  createSystem,
  defaultConfig,
} from '@chakra-ui/react';
import Layout from './layout/Layout';
import Home from './components/Home';
import TasksPage from './components/TasksPage';
import StatusPage from './components/StatusPage';
import { ColorModeProvider } from './components/ui/color-mode';
import './App.css';

const system = createSystem(defaultConfig);

function App() {
  return (
    <ChakraProvider value={system}>
      <ColorModeProvider>
        <Router>
          <Layout>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/tasks" element={<TasksPage />} />
              <Route path="/status" element={<StatusPage />} />
            </Routes>
          </Layout>
        </Router>
      </ColorModeProvider>
    </ChakraProvider>
  );
}

export default App;
