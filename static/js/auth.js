export function authHeader() {
  const tok = localStorage.getItem('bomdb_token');
  return tok ? { 'Authorization': `Bearer ${tok}` } : {};
}
