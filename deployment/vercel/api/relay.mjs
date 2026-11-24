import {handle} from '../lib/core.mjs';
import {cloudHandle} from '../lib/cloud.mjs';
export default {fetch(request){return cloudHandle(request,process.env,handle);}};
