const $ = (s) => document.querySelector(s);
const API = (window.CLIPFORGE_CONFIG?.API_BASE || "").replace(/\/$/, "");
let currentVideo = null;
let clipCounter = 0;
let captions = true;

function toast(msg){const t=$("#toast");t.textContent=msg;t.classList.add("show");clearTimeout(t._x);t._x=setTimeout(()=>t.classList.remove("show"),2600)}
function secToClock(v){v=Math.max(0,Math.floor(Number(v)||0));const h=Math.floor(v/3600),m=Math.floor(v%3600/60),s=v%60;return h?`${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`:`${m}:${String(s).padStart(2,"0")}`}
function parseTime(v){v=String(v||"").trim();if(/^\d+(\.\d+)?$/.test(v))return Number(v);const p=v.split(":").map(Number);if(p.some(Number.isNaN))return NaN;if(p.length===2)return p[0]*60+p[1];if(p.length===3)return p[0]*3600+p[1]*60+p[2];return NaN}
function addClip(start=0,end=Math.min(30,currentVideo?.duration||30),label=""){
  clipCounter++; const row=document.createElement("div"); row.className="clip-row";
  row.innerHTML=`<div class="clip-num">${clipCounter}</div><label class="field"><span>START</span><input class="start" value="${secToClock(start)}" inputmode="decimal"></label><label class="field"><span>END</span><input class="end" value="${secToClock(end)}" inputmode="decimal"></label><button class="remove" aria-label="Remove clip">×</button><input class="label" type="hidden" value="${String(label).replaceAll('"','&quot;')}">`;
  row.querySelector(".remove").onclick=()=>{row.remove();renumber()}; $("#clips").appendChild(row); renumber();
}
function renumber(){[...document.querySelectorAll(".clip-row")].forEach((r,i)=>r.querySelector(".clip-num").textContent=i+1)}
function setBusy(on){$("#analyzeBtn").disabled=on;$("#analyzeBtn span").textContent=on?"Finding…":"Find video"}

async function api(path, options={}){
  const r=await fetch(API+path,{headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
  let data={};try{data=await r.json()}catch{}
  if(!r.ok)throw new Error(data.detail||data.message||`Request failed (${r.status})`);return data;
}

$("#analyzeBtn").onclick=async()=>{
  const url=$("#url").value.trim();if(!url)return toast("Paste a YouTube URL first.");
  setBusy(true);try{
    const d=await api("/api/analyze",{method:"POST",body:JSON.stringify({url})});currentVideo=d;
    $("#thumb").src=d.thumbnail||"";$("#videoTitle").textContent=d.title;$("#videoUploader").textContent=d.uploader;$("#durationBadge").textContent=secToClock(d.duration);
    $("#resolutionPills").innerHTML=(d.resolutions||[]).slice(0,6).map(x=>`<span class="pill">${x}p</span>`).join("")||`<span class="pill">Best available</span>`;
    $("#videoCard").classList.remove("hidden");$("#studio").classList.remove("hidden");$("#clips").innerHTML="";clipCounter=0;
    if(d.chapters?.length){$("#chapterArea").classList.remove("hidden");$("#chapters").innerHTML="";d.chapters.forEach(c=>{const b=document.createElement("button");b.className="chapter-chip";b.textContent=`${secToClock(c.start)} · ${c.title}`;b.onclick=()=>addClip(c.start,c.end,c.title);$("#chapters").appendChild(b)})}else{$("#chapterArea").classList.add("hidden")}
    addClip(0,Math.min(30,d.duration));d.warning&&toast(d.warning);$("#videoCard").scrollIntoView({behavior:"smooth",block:"center"});
  }catch(e){toast(e.message)}finally{setBusy(false)}
};
$("#addClip").onclick=()=>addClip(0,Math.min(30,currentVideo?.duration||30));
$("#captionToggle").onclick=()=>{captions=!captions;$("#captionToggle").classList.toggle("on",captions);$("#captionToggle").setAttribute("aria-pressed",String(captions));$("#captionLangRow").style.opacity=captions?1:.35;$("#captionModeRow").style.opacity=captions?1:.35};

function collectClips(){
  return [...document.querySelectorAll(".clip-row")].map((r,i)=>({start:parseTime(r.querySelector(".start").value),end:parseTime(r.querySelector(".end").value),label:r.querySelector(".label").value||`clip-${i+1}`}));
}

$("#processBtn").onclick=async()=>{
  if(!currentVideo)return toast("Find a video first.");const clips=collectClips();if(!clips.length)return toast("Add at least one clip.");
  for(const [i,c] of clips.entries()){if(!Number.isFinite(c.start)||!Number.isFinite(c.end)||c.end<=c.start)return toast(`Check timestamps in clip ${i+1}.`);if(c.end>currentVideo.duration+1)return toast(`Clip ${i+1} ends after the video.`)}
  const btn=$("#processBtn");btn.disabled=true;$("#donePanel").classList.add("hidden");$("#progressPanel").classList.remove("hidden");$("#progressPanel").scrollIntoView({behavior:"smooth",block:"center"});
  try{
    const j=await api("/api/process",{method:"POST",body:JSON.stringify({url:$("#url").value.trim(),clips,quality:$("#quality").value,captions,caption_language:$("#captionLang").value.trim()||"en",caption_mode:$("#captionMode").value})});poll(j.id);
  }catch(e){btn.disabled=false;$("#progressPanel").classList.add("hidden");toast(e.message)}
};

async function poll(id){
  try{
    const j=await api(`/api/job/${id}`);const p=Math.max(0,Math.min(100,j.progress||0));$("#progressPct").textContent=`${p}%`;$("#progressBar").style.width=`${p}%`;$("#progressMessage").textContent=j.message||j.status;
    if(j.status==="done"){
      $("#processBtn").disabled=false;$("#progressPanel").classList.add("hidden");$("#donePanel").classList.remove("hidden");const mb=j.size_bytes?` · ${(j.size_bytes/1048576).toFixed(1)} MB`:"";$("#doneMeta").textContent=`Your ZIP is ready${mb}. Temporary server files expire automatically.`;$("#downloadBtn").href=API+j.download_url;$("#donePanel").scrollIntoView({behavior:"smooth",block:"center"});return;
    }
    if(j.status==="error")throw new Error(j.message||"Processing failed.");setTimeout(()=>poll(id),1500);
  }catch(e){$("#processBtn").disabled=false;$("#progressPanel").classList.add("hidden");toast(e.message)}
}

$("#creatorBtn").onclick=()=>{const m=$("#creatorModal");m.classList.add("open");m.setAttribute("aria-hidden","false");const p=$("#particles");p.innerHTML="";for(let i=0;i<38;i++){const s=document.createElement("i");s.className="spark";s.style.left="50%";s.style.top="50%";const a=Math.random()*Math.PI*2,d=80+Math.random()*180;s.style.setProperty("--x",`${Math.cos(a)*d}px`);s.style.setProperty("--y",`${Math.sin(a)*d}px`);s.style.animationDelay=`${Math.random()*150}ms`;p.appendChild(s)}};
$("#closeCreator").onclick=()=>$("#creatorModal").classList.remove("open");$("#creatorModal").onclick=e=>{if(e.target.id==="creatorModal")e.currentTarget.classList.remove("open")};
$("#themePulse").onclick=()=>{document.body.animate([{filter:"brightness(1)"},{filter:"brightness(1.25) saturate(1.2)"},{filter:"brightness(1)"}],{duration:700});};
