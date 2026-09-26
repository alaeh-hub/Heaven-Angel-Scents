import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { matchPath, useLocation } from 'react-router';
import { fetchSite, sendGeneralInquiry, withCsrfRetry } from '../api.js';
import InquirySheet from './detail/InquirySheet.jsx';

const SiteContext = createContext({ site: null, openInquiry: () => {} });

/**
 * Portal-wide facts (contact details, reply time, catalog counts, see
 * routes/portal.py's api_site) plus the one general inquiry form, not
 * tied to any package. Any page can open it with
 * `useSite().openInquiry({ message })`. Lives above the routes so the
 * form and the facts survive moving between pages.
 */
export function InquiryProvider({ children }) {
  const { pathname } = useLocation();
  const slug = matchPath('/partner-portal/:slug/*', pathname)?.params.slug;
  const [site, setSite] = useState(null);
  const [open, setOpen] = useState(false);
  const [initialMessage, setInitialMessage] = useState('');
  const csrfToken = useRef('');

  useEffect(() => {
    if (!slug) return undefined;
    const controller = new AbortController();
    fetchSite(slug, controller.signal)
      .then((result) => {
        csrfToken.current = result.csrf_token;
        setSite(result);
      })
      // Not fatal: the page still works, the form fetches again on send.
      .catch(() => {});
    return () => controller.abort();
  }, [slug]);

  const openInquiry = useCallback(({ message = '' } = {}) => {
    setInitialMessage(message);
    setOpen(true);
  }, []);
  const close = useCallback(() => setOpen(false), []);

  const submit = (payload) => withCsrfRetry(
    csrfToken,
    (token) => sendGeneralInquiry(slug, payload, token),
    async () => (await fetchSite(slug)).csrf_token,
  );

  return (
    <SiteContext.Provider value={{ site, openInquiry }}>
      {children}
      {slug && (
        <InquirySheet
          title="Send an inquiry"
          intro="Ask about pricing, a custom mix, or becoming a partner. Our team follows up by phone or email."
          partnerTypes={site?.partner_types || ['Distributor', 'Reseller']}
          contactMethods={site?.contact_methods || ['Call', 'SMS', 'Viber', 'Email']}
          replyTime={site?.reply_time}
          initialMessage={initialMessage}
          open={open}
          onClose={close}
          submit={submit}
        />
      )}
    </SiteContext.Provider>
  );
}

export const useSite = () => useContext(SiteContext);
