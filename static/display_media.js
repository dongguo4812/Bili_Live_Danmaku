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
  function appendVideoTitle(parent,title){
    const text=String(title||"未知视频");
    const viewport=node("span","md-video-title");
    if(Array.from(text).length>10){
      const track=node("span","md-video-title-track");
      track.appendChild(node("span","",text));
      track.appendChild(node("span","",text));
      viewport.appendChild(track);
      viewport.classList.add("is-scrolling");
    }else{
      viewport.textContent=text;
    }
    parent.appendChild(viewport);
  }
  function appendMediaLine(parent,item,type){
    if(type==="music"){
      const title=String(item.title||"未知歌曲");
      const fullTitle=item.artist?title+"-"+item.artist:title;
      parent.appendChild(node("span","md-inline-title",fullTitle));
    }else{
      appendVideoTitle(parent,item.title||item.bvid||"未知视频");
      if(item.bvid) parent.appendChild(node("span","md-bvid",item.bvid));
    }
    if(item.uname) parent.appendChild(node("span","md-requester",item.uname));
  }
  function queueList(parent,queue,type){
    const list=node("div","md-queue");
    queue.forEach((item,index)=>{
      const row=node("div","md-queue-item"+(index===0?" is-next":""));
      row.appendChild(node("span","md-queue-index",String(index+1)+"."));
      const main=node("div","md-queue-main");
      appendMediaLine(main,item,type);
      row.appendChild(main);
      list.appendChild(row);
    });
    if(queue.length) parent.appendChild(list);
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

    const currentBox=section(root,"正在播放 ▶","current");
    const currentHeading=currentBox.querySelector(".md-heading");
    if(current){
      appendMediaLine(currentHeading,current,type);
    }else{
      currentHeading.appendChild(node("span","md-empty",type==="music"?"暂无点播歌曲":"暂无点播视频"));
    }

    if(options.moduleState==="paused"){
      root.appendChild(node("div","md-paused-status",type==="music"?"⏸ 歌曲点播已暂停":"⏸ 视频点播已暂停"));
    }

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

    const nextBox=section(root,"接下来播放","next");
    const nextHeading=nextBox.querySelector(".md-heading");
    nextHeading.appendChild(node("span","md-next-icon","Ⅱ"));
    if(queue.length){
      queueList(nextBox,queue,type);
    }else{
      nextHeading.appendChild(node("span","md-empty",type==="music"?"暂无待播歌曲":"暂无待播视频"));
    }
  }
  global.MediaDisplay={render:render};
})(window);
