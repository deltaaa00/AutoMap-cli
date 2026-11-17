"""Country targeting, PNG masks, and real reveal animation frames."""
import base64
import importlib
import io

import numpy as np
import pytest
from moviepy import ImageClip
from PIL import Image, ImageDraw

renderer = importlib.import_module('map.auto_map')


def png(image):
    output=io.BytesIO()
    image.save(output,format='PNG')
    return base64.b64encode(output.getvalue()).decode()


@pytest.mark.parametrize('key,x,y,expected', [('one',0,0,'one'),('',.5,.5,'one'),('',0,0,''),('',True,.5,''),('',None,.5,''),('',float('nan'),.5,'')])
def test_nearest_country_selection_rejects_remote_or_invalid_points(web,key,x,y,expected):
    assert web._resolve_reveal_country_key({'one':[(50,50)],'empty':[]},100,100,key,x,y)==expected


@pytest.mark.parametrize('raw,expected', [(None,None),('',None),('data:image/png;base64,',None),('!!',None),(base64.b64encode(b'not png').decode(),None)])
def test_invalid_uploaded_map_cannot_be_used_as_reveal_mask(web,raw,expected):
    assert web._decode_image_size(raw)==expected


def test_reveal_masks_and_outline_endpoints_use_country_geometry(web,tmp_path,monkeypatch):
    maps=tmp_path/'resources'/'maps'
    maps.mkdir(parents=True)
    country=Image.new('RGBA',(64,64),(0,0,0,0))
    ImageDraw.Draw(country).rectangle((3,3,12,12),fill='white')
    for filename in ['default_map.png','world_map.png','world_countries_map.png']:
        country.save(maps/filename)
    monkeypatch.setattr(web,'BASE_DIR',tmp_path)
    from map import constants, world_countries_coords
    monkeypatch.setattr(constants,'europe_coords',{'one':[(5,5)]})
    monkeypatch.setattr(constants,'world_coords',{'continent':[(5,5)]})
    monkeypatch.setattr(world_countries_coords,'world_countries_coords',{'one':[(5,5)]})
    monkeypatch.setattr(web,'_ZOOM_REF_DIMENSIONS',{'europe':(64,64),'world':(64,64)})
    assert web._region_mask_from_index(maps/'default_map.png',{'one':[(5,5)]},'missing') is None
    assert web._world_country_floodfill_mask([]) is None
    assert web._extract_country_mask_image('invalid','one') is None
    assert web._extract_country_mask_image('world','continent') is None
    assert web._extract_country_mask_image('world','continent',allow_world_continent_fallback=True).getbbox()
    assert web._world_country_floodfill_mask([(5,5)]).getpixel((5,5))==255
    assert web._extract_country_mask_image('world','one').getbbox()
    assert web._extract_country_reveal_mask(0,4,'one',.5,.5)==''
    assert web._extract_country_reveal_mask(64,64,'unknown',1,1)==''
    for width,height in [(32,64),(128,64)]:
        encoded=web._extract_country_reveal_mask(width,height,'one',.1,.1)
        mask=Image.open(io.BytesIO(base64.b64decode(encoded)))
        assert mask.mode=='L' and mask.size==(width,height) and mask.getbbox()
        assert web._decode_image_size('data:image/png;base64,'+encoded)==(width,height)
        assert web._extract_country_reveal_mask(width,height,'one',.1,.1)==encoded
    client=web.app.test_client()
    countries=client.get('/api/zoom_countries').json
    assert countries['europe'][0]['key']=='one' and countries['world'][0]['x']==pytest.approx(5/64,abs=.0001)
    assert client.get('/api/zoom_country_outline').status_code==404
    assert client.get('/api/zoom_country_outline?map=world&key=missing').status_code==404
    for solid,width in [('0',360),('1',720)]:
        route=f'/api/zoom_country_outline?map=europe&key=one&solid={solid}'
        first=client.get(route)
        assert first.status_code==200 and Image.open(io.BytesIO(first.data)).width==width
        assert client.get(route).data==first.data


@pytest.mark.parametrize('effects',[False,True])
def test_reveal_animation_covers_then_reveals_original_pixels(worker,tmp_path,effects):
    path=tmp_path/'map.png'
    Image.new('RGBA',(96,96),(200,100,50,255)).save(path)
    mask=np.zeros((96,96,1),dtype=np.float32)
    mask[20:75,20:75]=1
    # A hole exercises dot placement inside a non-convex country.
    mask[40:55,40:55]=0
    clip=worker._build_reveal_map_clip(str(path),mask,96,96,4,effects,True,.5,.5,.8,.5,2,'#12ab34')
    try:
        for t in [0,.3,.6,1.5,2,2.125,3]:
            frame=clip.get_frame(t)
            assert frame.shape[2]==3 and frame.dtype==np.uint8
            assert clip.mask.get_frame(t).shape==frame.shape[:2]
        offset=24 if effects else 0
        assert tuple(clip.get_frame(0)[70+offset,25+offset])==(18,171,52)
        assert tuple(clip.get_frame(3)[70+offset,25+offset])==(200,100,50)
        assert len(clip.rust_reveal_spec)==10
    finally:clip.close()


@pytest.mark.parametrize('raw,expected', [('#abc',(170,187,204)),('#aAbBcC',(170,187,204)),('bad-color',(0,0,0)),(None,(0,0,0))])
def test_reveal_cover_colors(worker,raw,expected):
    assert worker._parse_reveal_color(raw)==expected


@pytest.mark.parametrize('raw', ['',None,'data:image/png,xx','data:image/png;base64,!!',png(Image.new('L',(4,4),0)),base64.b64encode(b'invalid').decode()])
def test_invalid_worker_masks_are_ignored(worker,raw):
    assert worker._decode_reveal_mask(raw,8,8) is None


def test_worker_decodes_valid_mask_and_impact_fx_preserves_frame_contract(worker):
    mask=worker._decode_reveal_mask('data:image/png;base64,'+png(Image.new('L',(4,4),255)),8,8)
    assert mask.shape==(8,8,1) and np.all(mask==1)
    frame=np.zeros((96,96,3),dtype=np.uint8)
    frame[:,:,0]=np.arange(96,dtype=np.uint8)
    clip=ImageClip(frame).with_duration(3)
    effect=worker._apply_reveal_impact_fx(clip,1,96,96)
    try:
        assert np.array_equal(effect.get_frame(0),frame)
        assert np.array_equal(effect.get_frame(2),frame)
        for time in [1,1.04,1.1,1.2,1.4,1.59]:
            assert effect.get_frame(time).shape==frame.shape
        assert not np.array_equal(effect.get_frame(1),frame)
    finally:clip.close();effect.close()


def test_extrema_masks_highlight_only_selected_region():
    interior=np.zeros((64,64),dtype=bool)
    interior[3:13,3:13]=True
    assert renderer._floodfill_bool_mask(interior,[(5,5)],200).sum()==100
    assert renderer._floodfill_bool_mask(interior,[(5,5)],50) is None
    assert renderer._floodfill_bool_mask(interior,[(60,60)],200) is None
    base=np.zeros((64,64,4),dtype=np.uint8)
    base[interior]=(255,255,255,255)
    assert renderer._build_extrema_highlight_layer((64,64),None,None,'world_countries',{},None,None,None) is None
    layer=renderer._build_extrema_highlight_layer((64,64),1,None,'world_countries',{'one':[(5,5)]},None,None,None,Image.fromarray(base))
    assert layer.getpixel((5,5))[3]>0
    assert layer.getpixel((60,60))[3]==0
    flat=np.flatnonzero(interior.ravel())
    layer=renderer._build_extrema_highlight_layer((64,64),1,1,'default',{'one':[(5,5)]},flat,np.ones(flat.size,dtype=np.uint16),None)
    assert layer.getpixel((5,5))[3]>0
