export class ApiError extends Error {
  constructor(message,status=0,code='NETWORK'){super(message);this.status=status;this.code=code;}
}
// Production integration point. The front end never reads the demo server's storage.
export const api={
  base:window.VTB_API_BASE||'/api/ui',
  token:sessionStorage.getItem('vtb-mvp-session')||'',
  async request(path,{method='GET',body,timeout=9000}={}){
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),timeout);
    try{
      const response=await fetch(this.base+path,{method,signal:controller.signal,headers:{'Content-Type':'application/json',...(this.token?{Authorization:'Bearer '+this.token}:{})},...(body?{body:JSON.stringify(body)}:{})});
      let data;try{data=await response.json();}catch{throw new ApiError('Сервер вернул неожиданный ответ. Проверьте подключение API.',response.status,'INVALID_RESPONSE');}
      if(!response.ok)throw new ApiError(data.error?.message||'Не удалось выполнить действие.',response.status,data.error?.code);
      return data;
    }catch(e){if(e instanceof ApiError)throw e;throw new ApiError(e.name==='AbortError'?'Сервер отвечает дольше обычного. Результат действия пока неизвестен.':'Нет связи с сервером. Проверьте подключение и повторите запрос.');}
    finally{clearTimeout(timer);}
  },
  async login(userId){const result=await this.request('/demo/login',{method:'POST',body:{userId}});this.token=result.token;sessionStorage.setItem('vtb-mvp-session',result.token);return result.snapshot;},
  async logout(){try{await this.request('/demo/logout',{method:'POST'});}finally{this.token='';sessionStorage.removeItem('vtb-mvp-session');sessionStorage.removeItem('vtb-pending-payment');}},
  snapshot(){return this.request('/snapshot');},
  mutate(path,body,method='POST'){return this.request(path,{method,body});}
};
