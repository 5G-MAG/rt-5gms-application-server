
#==============================================================================
# 5G-MAG Reference Tools: Build & POST CMCD v2 (response-mode) JSON
#==============================================================================
#
# File: cmcd_response_json.lua
# License: 5G-MAG Public License (v1.0)
# Author: Shilin Ding
# Copyright: (C) 2026 Qualcomm Corporation
#
#==============================================================================

local cjson = require "cjson.safe"
local http  = require "resty.http"

ngx.log(ngx.NOTICE, ">>> CMCD LUA (response-mode) TRIGGERED <<<")

-- ---------------- parse v1 k/v (from query & headers) ----------------
local function parse_kv(s)
  if not s or s == "" then return {} end
  local t = {}
  for part in s:gmatch("([^,]+)") do
    local k, v = part:match("^%s*([%w%-_]+)%s*=%s*(.-)%s*$")
    if not k then
      local token = part:match("^%s*([%w%-_]+)%s*$")
      if token then t[token] = true end
    else
      v = v:gsub('^"(.*)"$', "%1")   -- ???????
      t[k] = tonumber(v) or v        -- ??????,????????/??token
    end
  end
  return t
end

local function extract_v1()
  local v1, args = {}, (ngx.req.get_uri_args() or {})

  -- 1) ?? CMCD=... ??(??????)
  local cmcd = args.CMCD or args.cmcd
  if type(cmcd) == "table" then
    for _, v in ipairs(cmcd) do for k,x in pairs(parse_kv(v)) do v1[k]=x end end
  elseif type(cmcd) == "string" then
    for k,x in pairs(parse_kv(cmcd)) do v1[k]=x end
  end

  -- 2) ?????? query(?? response-mode ???)
  local whitelist = {
    -- ?? QoE
    "br","d","tb","bl","dl","mtp","nor","nrr","rtp",
    "su","sid","cid","rid","pr","sf","st","v","sta","ot","ts",
    -- response-mode ??
    "ttfb","ttlb","rc","url","sz"
  }
  for _, k in ipairs(whitelist) do
    local val = args[k]
    if val ~= nil and v1[k] == nil then
      local first = (type(val)=="table") and val[1] or val
      v1[k] = tonumber(first) or tostring(first)
    end
  end

  -- 3) ?? CMCD* headers(v1 k/v)
  local hdrs = ngx.req.get_headers()
  for _, hk in ipairs({"CMCD","CMCD-Object","CMCD-Request","CMCD-Status","CMCD-Session",
                       "cmcd","cmcd-object","cmcd-request","cmcd-status","cmcd-session"}) do
    local hv = hdrs[hk]
    if hv then
      if type(hv)=="table" then
        for _,v in ipairs(hv) do for k,x in pairs(parse_kv(v)) do v1[k]=x end end
      else
        for k,x in pairs(parse_kv(hv)) do v1[k]=x end
      end
    end
  end
  return v1
end

-- ---------------- build CMCD v2 response-mode JSON ----------------
local function build_response_v2(v1)
  local now_ms = math.floor(ngx.now()*1000)

  local function num(x)  return (x ~= nil) and tonumber(x) or nil end
  local function str(x)  return (x ~= nil) and tostring(x) or nil end
  local function bool(x)
    if x == true or x == 1 or x == "1" or x == "true" then return true end
    if x == false or x == 0 or x == "0" or x == "false" then return false end
    return nil
  end

  local resp = {
    v   = 2,
    ts  = num(v1.ts) or now_ms,
    sid = str(v1.sid),
    cid = str(v1.cid),
    st  = str(v1.st),                  -- 'v'|'a'|'i'
    ot  = str(v1.ot),                  -- 'v'|'a'|'m'|'o'
    sf  = str(v1.sf),                  -- 'd' or 'l'
    su  = bool(v1.su),
    sta = str(v1.sta),
    rid = str(v1.rid),

    -- Response ??
    url  = str(v1.url),
    rc   = num(v1.rc),
    ttfb = num(v1.ttfb),
    ttlb = num(v1.ttlb),
    sz   = num(v1.sz),

    -- QoE ??(????)
    br = num(v1.br),
    d  = num(v1.d),
    bl = num(v1.bl),
    tb = num(v1.tb),

    -- ????
    dl  = num(v1.dl),
    mtp = num(v1.mtp),
    rtp = num(v1.rtp),
    nor = str(v1.nor),
  }

  -- ??????(?????,???? return nil)
  local missing = {}
  for _, key in ipairs({"sid","cid","st","ot"}) do
    if not resp[key] or resp[key] == "" then table.insert(missing, key) end
  end
  if #missing > 0 then
    ngx.log(ngx.WARN, "[cmcd][response] missing keys: ", table.concat(missing, ","))
    -- ???????:return nil
  end

  -- ?? nil,?? JSON ?? null
  for k, v in pairs(resp) do
    if v == nil then resp[k] = nil end
  end
  return resp
end

-- ---------------- build Origin/Referer(??????????) ----------------
local function build_origin_headers_in_request()
  -- ?????????(rewrite/access/content),??? timer ?????
  local in_hdrs = ngx.req.get_headers()
  local origin  = in_hdrs["Origin"]  or in_hdrs["origin"]
  local referer = in_hdrs["Referer"] or in_hdrs["referer"]

  -- ?????????(init_by_lua ???)
  local dict = ngx.shared.cmcd_cfg
  if (not origin or origin == "") and dict then
    origin = dict:get("spoof_origin")
  end
  if (not referer or referer == "") and dict then
    referer = dict:get("spoof_referer")
  end

  -- ????????????,?? Fluentd ? nil/"" ????
  if not origin or origin == "" then
    local scheme = ngx.var.scheme or "http"
    local host   = ngx.var.server_name or ngx.var.host or ngx.var.server_addr or "127.0.0.1"
    local port   = (dict and dict:get("spoof_origin_port")) or "8080"
    origin = string.format("%s://%s:%s", scheme, host, port)
  end
  if not referer or referer == "" then
    referer = origin .. "/player"
  end

  ngx.log(ngx.NOTICE, "[cmcd][response] using Origin=", origin, " Referer=", referer)
  return { ["Origin"] = origin, ["Referer"] = referer }
end

-- ---------------- async POST(? timer ?????????) ----------------
local function async_post_json(premature, url, payload, extra_headers)
  if premature then return end
  local httpc = http.new()
  httpc:set_timeout(1500)
  local body = cjson.encode(payload)

  -- ?? headers(timer ????? ngx.req/ngx.var)
  local hdrs = { ["Content-Type"] = "application/json" }
  if extra_headers then
    for k, v in pairs(extra_headers) do
      if v and v ~= "" then hdrs[k] = v end
    end
  end

  local res, err = httpc:request_uri(url, {
    method    = "POST",
    body      = body,
    headers   = hdrs,
    keepalive = true
  })
  if not res then
    ngx.log(ngx.ERR, "[cmcd][response] POST failed: ", err or "nil", " url=", url)
    return
  end
  ngx.log(ngx.NOTICE, "[cmcd][response] POST resp ", res.status, " len=", res.body and #res.body or 0)
  if res.status >= 300 then
    ngx.log(ngx.WARN, "[cmcd][response] non-2xx: ", res.status, " body=", res.body or "")
  end
end

-- ---------------- main ----------------
local v1 = extract_v1()
if next(v1) then
  local dict = ngx.shared.cmcd_cfg
  local url  = dict and (dict:get("collector_response_url") or dict:get("collector_event_url"))
  if not url or url == "" then
    ngx.log(ngx.ERR, "[cmcd][response] collector url not configured")
    return
  end
  url = (url:gsub("/+$",""))
  if not url:match("/cmcd/response%-mode$") then
    url = url:gsub("/cmcd/event%-mode$","/cmcd/response-mode")
    if not url:match("/cmcd/response%-mode$") then
      url = url .. "/cmcd/response-mode"
    end
  end

  local resp = build_response_v2(v1)
  if not resp then
    return
  end

  -- **??**:????????? Origin/Referer,??? timer
  local origin_headers = build_origin_headers_in_request()

  ngx.log(ngx.NOTICE, "[cmcd][response] v2 payload = ", cjson.encode(resp))

  -- ? url/resp/headers ?????? timer;??????? ngx.req / ngx.var
  local ok, err = ngx.timer.at(0, async_post_json, url, resp, origin_headers)
  if not ok then
    ngx.log(ngx.ERR, "[cmcd][response] failed to schedule post timer: ", err or "nil")
  end
else
  ngx.log(ngx.WARN, "[cmcd][response] no CMCD found: ", ngx.var.request_uri or "")
end
