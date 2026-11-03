/* B站歌曲/视频统一展示组件。仅负责展示，不修改播放与队列业务。 */
(function(global){
  "use strict";
  function node(tag,className,text){
    const el=document.createElement(tag);
    if(className) el.className=className;
    if(text!==undefined&&text!==null) el.textContent=String(text);
    return el;
  }
  function section(root,title,kind){
    const box=node("section","md-section");
    box.appendChild(node("div","md-heading"+(kind==="next"?" next":""),title));
    root.appendChild(box);
    return box;
  }
  function details(parent,item,type){
    if(!item) return;
    const meta=node("div","md-meta");
    if(type==="music"&&item.artist) meta.appendChild(node("span","", "歌手："+item.artist));
    if(type==="video"&&item.bvid) meta.appendChild(node("span","",item.bvid));
    if(item.uname) meta.appendChild(node("span","requester","点播人："+item.uname));
    if(meta.childNodes.length) parent.appendChild(meta);
  }
  function titleLine(parent,item,type,emptyText){
    if(!item){
      parent.appendChild(node("div","md-empty",emptyText));
      return;
    }
    parent.appendChild(node("div","md-primary",item.title|| (type==="video"?item.bvid:"未知歌曲")));
    details(parent,item,type);
  }
  function renderSongWidget(parent,widget){
    if(!widget||widget.available===false) return;
    const title=String(widget.name||widget.title||"").trim();
    const artist=String(widget.singer||widget.artist||"").trim();
    const cover=String(widget.picUrl||widget.cover||"").trim();
    if(!title&&!artist&&!cover) return;
    const card=node("div","md-song-widget");
    if(/^https?:\\/\\//i.test(cover)){
      const image=node("img","md-song-cover");
      image.src=cover;
      image.alt=title?title+" 封面":"歌曲封面";
      image.loading="eager";
      image.referrerPolicy="no-referrer";
      image.addEventListener("error",()=>{image.remove();});
      card.appendChild(image);
    }
    const info=node("div","md-song-info");
    info.appendChild(node("div","md-song-title",title||"未知歌曲"));
    if(artist) info.appendChild(node("div","md-song-artist","歌手："+artist));
    const album=String(widget.albumName||widget.album||"").trim();
    if(album) info.appendChild(node("div","md-song-album","专辑："+album));
    const duration=Number(widget.duration);
    const progress=Number(widget.progress);
    if(Number.isFinite(duration)&&duration>0){
      const progressText=Number.isFinite(progress)&&progress>=0
        ? Math.floor(progress/1000)+" / "+Math.floor(duration/1000)+" 秒"
        : "时长："+Math.floor(duration/1000)+" 秒";
      info.appendChild(node("div","md-song-progress",progressText));
    }
    card.appendChild(info);
    parent.appendChild(card);
  }
  function normalizeNotices(data,type){
    const raw=type==="music"?(data.events||[]):(data.operations||[]);
    const now=Date.now()/1000;
    return raw.filter(item=>{
      if(!item||!item.message) return false;
      const stamp=Number(item.timestamp);
      return !Number.isFinite(stamp)||stamp<=0||(now-stamp>=0&&now-stamp<=10);
    }).slice(-8);
  }
  function render(root,type,data,options){
    if(!root||!data) return;
    options=options||{};
    root.replaceChildren();
    root.classList.add("media-display");
    root.classList.toggle("md-compact",!!options.compact);
    if(type==="music"&&data.song_request_enabled===false){
      root.appendChild(node("div","md-request-status","⛔ 当前已关闭点歌"));
    }else if(type==="music"&&options.showInstruction!==false){
      root.appendChild(node("div","md-instruction","B站歌曲点播指令：点歌 歌名-歌手名"));
    }else if(type==="video"&&options.showInstruction!==false){
      root.appendChild(node("div","md-instruction","B站视频点播指令：点播 BVxxxx"));
    }

    const current=data.current||null;
    const queue=Array.isArray(data.queue)?data.queue:[];
    const next=queue.length?queue[0]:null;

    if(type==="music"&&data.widget){
      const widgetBox=section(root,"当前歌曲信息","widget");
      renderSongWidget(widgetBox,data.widget);
    }

    const currentBox=section(root,"正在播放 ▶","current");
    titleLine(currentBox,current,type,type==="music"?"暂无机器人点播歌曲":"等待点播视频");

    const nextBox=section(root,"接下来播放","next");
    titleLine(nextBox,next,type,type==="music"?"暂无待播歌曲":"暂无待播视频");

    const queueBox=section(root,"完整待播队列","queue");
    const list=node("div","md-queue");
    if(!queue.length){
      list.appendChild(node("div","md-empty",type==="music"?"暂无待播歌曲":"暂无待播视频"));
    }else{
      queue.forEach((item,index)=>{
        const row=node("div","md-queue-item"+(index===0?" is-next":""));
        row.appendChild(node("span","md-queue-index",String(index+1)+"." ));
        const main=node("div","md-queue-main");
        main.appendChild(node("div","",item.title||(type==="video"?item.bvid:"未知歌曲")));
        const meta=node("div","md-meta");
        if(type==="music"&&item.artist) meta.appendChild(node("span","","歌手："+item.artist));
        if(type==="video"&&item.bvid) meta.appendChild(node("span","",item.bvid));
        if(item.uname) meta.appendChild(node("span","requester","点播人："+item.uname));
        if(meta.childNodes.length) main.appendChild(meta);
        row.appendChild(main);
        list.appendChild(row);
      });
    }
    queueBox.appendChild(list);

    const notices=normalizeNotices(data,type);
    if(notices.length){
      const noticeBox=section(root,"点播通知","notices");
      const stack=node("div","md-notices");
      notices.forEach(item=>{
        const kind=item.kind==="success"?"success":item.kind==="failure"?"failure":"";
        const row=node("div","md-notice"+(kind?" "+kind:""));
        if(item.time) row.appendChild(node("span","md-notice-time",item.time));
        row.appendChild(document.createTextNode(item.message));
        stack.appendChild(row);
      });
      noticeBox.appendChild(stack);
    }
  }
  global.MediaDisplay={render:render};
})(window);
