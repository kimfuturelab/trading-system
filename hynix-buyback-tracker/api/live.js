const SOURCE_URL = 'https://script.google.com/macros/s/AKfycbznUeW68apO8MM2AEX_T_PZ2FfwrPGfojUIgDSDRXz-YRIzTbGbOUhb30BzUxue90qHQA/exec';

module.exports = async function handler(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).json({ ok: false, error: 'method_not_allowed' });
  }
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    const upstream = await fetch(SOURCE_URL, {
      method: 'GET',
      redirect: 'follow',
      signal: controller.signal,
      headers: { 'user-agent': 'hynix-buyback-tracker-v1/restore-20260827' }
    });
    clearTimeout(timer);
    const body = await upstream.text();
    if (!upstream.ok) {
      return res.status(502).json({ ok: false, error: `source_http_${upstream.status}` });
    }
    let data;
    try { data = JSON.parse(body); }
    catch { return res.status(502).json({ ok: false, error: 'source_invalid_json' }); }
    res.setHeader('Cache-Control', 'public, max-age=0, s-maxage=15, stale-while-revalidate=15');
    return res.status(200).json(data);
  } catch (error) {
    const code = error && error.name === 'AbortError' ? 'source_timeout' : 'source_fetch_failed';
    return res.status(502).json({ ok: false, error: code });
  }
};
