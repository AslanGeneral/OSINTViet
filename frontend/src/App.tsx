import React, { useState, useEffect } from 'react';
import { LanguageProvider } from './context/LanguageContext';
import { SiteConfigProvider } from './context/SiteConfigContext';
import { Header } from './components/Header';
import { Footer } from './components/Footer';
import { HomeView } from './views/HomeView';
import { ArticlesView } from './views/ArticlesView';
import { ToolView } from './views/ToolView';
import { AdminView } from './views/AdminView';
import { MacroBreakdownResponse, Article } from './types';

export const AppContent: React.FC = () => {
  // Support standalone admin subdomain or path /admin
  const initialTab = (() => {
    const path = window.location.pathname.toLowerCase();
    const host = window.location.hostname.toLowerCase();
    if (host.startsWith('admin.') || path === '/admin' || path.startsWith('/admin/')) {
      return 'admin';
    }
    return 'home';
  })();

  const [currentTab, setCurrentTab] = useState<'home' | 'articles' | 'tool' | 'admin'>(initialTab);

  // State for data handoff from Section II to Section III
  const [importedBaseData, setImportedBaseData] = useState<{
    breakdown: MacroBreakdownResponse;
    article: Article;
  } | null>(null);

  // Sync browser history / hash if needed
  useEffect(() => {
    const handlePopState = () => {
      const path = window.location.pathname.toLowerCase();
      if (path === '/admin') setCurrentTab('admin');
      else if (path === '/reading-room' || path === '/articles') setCurrentTab('articles');
      else if (path === '/indicating-room' || path === '/tool') setCurrentTab('tool');
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const handleTabChange = (tab: 'home' | 'articles' | 'tool' | 'admin') => {
    setCurrentTab(tab);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const handleHandoffToTool = (breakdown: MacroBreakdownResponse, article: Article) => {
    setImportedBaseData({ breakdown, article });
    setCurrentTab('tool');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const handleClearImportedData = () => {
    setImportedBaseData(null);
  };

  return (
    <div className="min-h-screen flex flex-col bg-white text-brand-dark antialiased">
      <Header currentTab={currentTab} setCurrentTab={handleTabChange} />

      <main className="flex-1">
        {currentTab === 'home' && (
          <HomeView
            onNavigateTab={handleTabChange}
            onHandoffToTool={handleHandoffToTool}
          />
        )}

        {currentTab === 'articles' && (
          <ArticlesView
            onHandoffToTool={handleHandoffToTool}
            onNavigateTab={handleTabChange}
          />
        )}

        {currentTab === 'tool' && (
          <ToolView
            importedBaseData={importedBaseData}
            onClearImportedData={handleClearImportedData}
          />
        )}

        {currentTab === 'admin' && (
          <AdminView />
        )}
      </main>

      <Footer />
    </div>
  );
};

export const App: React.FC = () => {
  return (
    <LanguageProvider>
      <SiteConfigProvider>
        <AppContent />
      </SiteConfigProvider>
    </LanguageProvider>
  );
};

export default App;
